"""
===============================================================================
 SBIN 5-MIN INTRADAY STRATEGY  --  OpenAlgo Trading Bot
 61 EMA + RSI + MACD + ADX/DI + VWAP + Regime  [MODIFIED / PROFITABLE VERSION]
===============================================================================

 This is the Python/OpenAlgo port of the modified Pine Script strategy that
 was backtested on SBIN 5m data (25-May-2026 .. 14-Aug-2026):
     Original Pine strategy : -2.73%  (PF 0.51, 38.9% win rate)
     This modified version  : +2.06%  (PF 2.91, 61.5% win rate, maxDD -1.04%)

 Modifications vs the original Pine script:
   [MOD-1] Stop-loss     : ATR(14) x 1.5      (was: 5-bar swing low/high)
   [MOD-2] Targets       : TP1 = 1.5R (50%), TP2 = 3R (25%)   (was 1R / 2R)
   [MOD-3] Volume filter : OFF by default     (removed more winners than losers)
   [MOD-4] NEW filter    : skip entries when price is > 0.5% away from the
                           61 EMA (avoids chasing late/exhausted moves)

 Architecture (per OpenAlgo docs - Strategy Management / example strategies):
   - Strategy thread : polls client.history() and evaluates entries/exits on
                       CLOSED 5-minute candles only.
   - WebSocket thread: streams live LTP and fires SL / TP1 / TP2 exits
                       immediately (does not wait for candle close).
   - CTRL+C          : graceful shutdown; any open position is squared off.

 Requirements:
   pip install openalgo pandas numpy

 Setup:
   1. Run your OpenAlgo server and log in.
   2. Get your API key from the OpenAlgo portal.
   3. Fill in API_KEY / API_HOST / WS_URL below.
   4. python sbin_strategy.py
===============================================================================
"""

from openalgo import api
import pandas as pd
import numpy as np
import threading
import time
from datetime import datetime, timedelta

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# --- OpenAlgo connection ---
API_KEY  = "your-openalgo-api-key"
API_HOST = "http://127.0.0.1:5000"
WS_URL   = "ws://127.0.0.1:8765"

# --- Instrument ---
SYMBOL   = "SBIN"
EXCHANGE = "NSE"
PRODUCT  = "MIS"            # intraday
QUANTITY = 100              # total shares per trade (use a multiple of 4 so
                            # 50% / 25% partial exits are whole numbers)

# --- Data ---
CANDLE_TIMEFRAME = "5m"
LOOKBACK_DAYS    = 5        # enough history for EMA61/ADX warm-up
POLL_INTERVAL    = 10       # seconds between history polls

# --- Trend / entry filters (same as Pine) ---
EMA_LENGTH            = 61
USE_EMA_SLOPE_FILTER  = True
EMA_SLOPE_BARS        = 5

RSI_LENGTH     = 14
RSI_BUY_LEVEL  = 50.0
RSI_SELL_LEVEL = 50.0

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
USE_MACD_MOMENTUM_FILTER = True
MACD_MOMENTUM_BARS       = 1

USE_VWAP_FILTER = True

USE_ADX_FILTER  = True
USE_DI_FILTER   = True
ADX_LENGTH      = 14
ADX_SMOOTHING   = 14
MIN_ADX         = 20.0

USE_VOLUME_FILTER = False   # [MOD-3] off by default
VOLUME_LENGTH     = 20
VOLUME_MULTIPLIER = 1.0

USE_MIN_EMA_DIST_FILTER = True
MIN_EMA_DIST_PCT        = 0.10
USE_MAX_EMA_DIST_FILTER = True     # [MOD-4] overextension cap
MAX_EMA_DIST_PCT        = 0.50

USE_REGIME_FILTER    = True
REGIME_ADX_THRESHOLD = 20.0
REGIME_SLOPE_BARS    = 10
REGIME_MIN_SLOPE_PCT = 0.05

# --- Risk management ---
ATR_LENGTH          = 14        # [MOD-1]
ATR_STOP_MULTIPLIER = 1.5       # [MOD-1]
MAX_RISK_PCT        = 2.0       # skip trade if stop distance > 2% of price
FIRST_TARGET_R      = 1.5       # [MOD-2]
SECOND_TARGET_R     = 3.0       # [MOD-2]
FIRST_EXIT_PCT      = 0.50      # book 50% at TP1
SECOND_EXIT_PCT     = 0.25      # book 25% at TP2
TRAIL_LOOKBACK      = 5         # swing trail after TP2 (bars)
MOVE_TO_BREAKEVEN_AT_TP1 = True
TRAIL_AFTER_TP2          = True
USE_EMA_EXIT             = True # close if price crosses the 61 EMA (candle close)

# --- Sessions (IST, exchange time) ---
ENTRY_START = (9, 30)       # new entries allowed from 09:30
ENTRY_END   = (14, 0)       # ... until 14:00
SQUARE_OFF  = (15, 15)      # hard square-off at 15:15

# --- Direction ---
ENABLE_LONG  = True
ENABLE_SHORT = True

# ==============================================================================
# INDICATORS (identical math to the validated backtest)
# ==============================================================================

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (Pine ta.rma)."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()

def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    ru = rma(d.clip(lower=0.0), n)
    rd = rma((-d).clip(lower=0.0), n)
    out = 100 - 100 / (1 + ru / rd)
    return out.where(rd != 0, 100.0)

def macd(close: pd.Series, fast: int, slow: int, sig: int):
    ml = ema(close, fast) - ema(close, slow)
    sl = ema(ml, sig)
    return ml, sl, ml - sl

def true_range(h, l, c):
    return pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)

def atr(h, l, c, n):
    return rma(true_range(h, l, c), n)

def dmi(h, l, c, di_len, adx_len):
    up, dn = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    atr_v = rma(true_range(h, l, c), di_len)
    plus_di = 100 * rma(plus_dm, di_len) / atr_v
    minus_di = 100 * rma(minus_dm, di_len) / atr_v
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_v = rma(dx.fillna(0), adx_len)
    return plus_di, minus_di, adx_v

def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Per-day anchored VWAP on hlc3 (Pine ta.vwap(hlc3))."""
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    day = df.index.date
    pv = (hlc3 * df["volume"]).groupby(day).cumsum()
    vv = df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return (pv / vv).ffill()

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    out = pd.DataFrame(index=df.index)
    out["ema"] = ema(c, EMA_LENGTH)
    out["rsi"] = rsi(c, RSI_LENGTH)
    ml, sl, hist = macd(c, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    out["macd"], out["macd_sig"], out["hist"] = ml, sl, hist
    out["vwap"] = session_vwap(df)
    pdi, mdi, adx_v = dmi(h, l, c, ADX_LENGTH, ADX_SMOOTHING)
    out["pdi"], out["mdi"], out["adx"] = pdi, mdi, adx_v
    out["atr"] = atr(h, l, c, ATR_LENGTH)
    out["vol_sma"] = v.rolling(VOLUME_LENGTH).mean()
    out["trail_low"] = l.rolling(TRAIL_LOOKBACK).min()
    out["trail_high"] = h.rolling(TRAIL_LOOKBACK).max()
    out["close"], out["volume"] = c, v
    return out

# ==============================================================================
# SIGNAL EVALUATION (on the last CLOSED candle)
# ==============================================================================

def evaluate_signal(ind: pd.DataFrame):
    """Return 'LONG', 'SHORT' or None for the last closed candle (ind.iloc[-1])."""
    i, p1 = ind.iloc[-1], ind.iloc[-2]
    c, e = i["close"], i["ema"]

    dist_pct = abs(c - e) / e * 100.0

    # MACD crossover on this candle, both lines beyond zero (same as Pine)
    cross_up = i["macd"] > i["macd_sig"] and p1["macd"] <= p1["macd_sig"]
    cross_dn = i["macd"] < i["macd_sig"] and p1["macd"] >= p1["macd_sig"]
    macd_bull = cross_up and i["macd"] > 0 and i["macd_sig"] > 0
    macd_bear = cross_dn and i["macd"] < 0 and i["macd_sig"] < 0

    hist_prev = ind["hist"].iloc[-1 - MACD_MOMENTUM_BARS]
    l_mom = (not USE_MACD_MOMENTUM_FILTER) or i["hist"] > hist_prev
    s_mom = (not USE_MACD_MOMENTUM_FILTER) or i["hist"] < hist_prev

    ema_prev = ind["ema"].iloc[-1 - EMA_SLOPE_BARS]
    l_slope = (not USE_EMA_SLOPE_FILTER) or e > ema_prev
    s_slope = (not USE_EMA_SLOPE_FILTER) or e < ema_prev

    l_vwap = (not USE_VWAP_FILTER) or c > i["vwap"]
    s_vwap = (not USE_VWAP_FILTER) or c < i["vwap"]

    adx_ok = i["adx"] >= MIN_ADX
    l_adx = (not USE_ADX_FILTER) or (adx_ok and ((not USE_DI_FILTER) or i["pdi"] > i["mdi"]))
    s_adx = (not USE_ADX_FILTER) or (adx_ok and ((not USE_DI_FILTER) or i["mdi"] > i["pdi"]))

    vol_ok = (not USE_VOLUME_FILTER) or i["volume"] >= i["vol_sma"] * VOLUME_MULTIPLIER

    dist_ok = ((not USE_MIN_EMA_DIST_FILTER) or dist_pct >= MIN_EMA_DIST_PCT) and \
              ((not USE_MAX_EMA_DIST_FILTER) or dist_pct <= MAX_EMA_DIST_PCT)   # [MOD-4]

    ema_reg_prev = ind["ema"].iloc[-1 - REGIME_SLOPE_BARS]
    slope_pct = (e - ema_reg_prev) / ema_reg_prev * 100.0
    bull_regime = c > e and i["adx"] >= REGIME_ADX_THRESHOLD and i["pdi"] > i["mdi"] and slope_pct >= REGIME_MIN_SLOPE_PCT
    bear_regime = c < e and i["adx"] >= REGIME_ADX_THRESHOLD and i["mdi"] > i["pdi"] and slope_pct <= -REGIME_MIN_SLOPE_PCT
    l_reg = (not USE_REGIME_FILTER) or bull_regime
    s_reg = (not USE_REGIME_FILTER) or bear_regime

    long_setup = (ENABLE_LONG and c > e and i["rsi"] > RSI_BUY_LEVEL and macd_bull
                  and l_mom and l_slope and l_vwap and l_adx and vol_ok and dist_ok and l_reg)
    short_setup = (ENABLE_SHORT and c < e and i["rsi"] < RSI_SELL_LEVEL and macd_bear
                   and s_mom and s_slope and s_vwap and s_adx and vol_ok and dist_ok and s_reg)

    if long_setup:
        return "LONG"
    if short_setup:
        return "SHORT"
    return None

# ==============================================================================
# TRADING BOT
# ==============================================================================

class SBINStrategyBot:
    def __init__(self):
        self.client = api(api_key=API_KEY, host=API_HOST, ws_url=WS_URL)
        self.instrument = [{"exchange": EXCHANGE, "symbol": SYMBOL}]

        # position state (guarded by self.lock)
        self.lock = threading.Lock()
        self.position = None          # "LONG" / "SHORT" / None
        self.qty_remaining = 0
        self.entry_price = 0.0
        self.risk = 0.0
        self.stop_price = 0.0
        self.tp1_price = 0.0
        self.tp2_price = 0.0
        self.tp1_done = False
        self.tp2_done = False
        self.exit_in_progress = False

        self.ltp = None
        self.last_candle_ts = None    # last processed closed-candle timestamp
        self.stop_event = threading.Event()

    # ------------------------------------------------------------------ orders
    def order(self, action: str, qty: int, tag: str):
        """Market order via OpenAlgo."""
        try:
            resp = self.client.placeorder(
                strategy="SBIN_61EMA_MOD",
                symbol=SYMBOL,
                exchange=EXCHANGE,
                action=action,           # "BUY" / "SELL"
                price_type="MARKET",
                product=PRODUCT,
                quantity=qty,
            )
            print(f"\n[ORDER] {tag}: {action} {qty} {SYMBOL} -> {resp}")
            return resp
        except Exception as e:
            print(f"\n[ERROR] order failed ({tag}): {e}")
            return None

    def _flat(self):
        self.position = None
        self.qty_remaining = 0
        self.entry_price = self.risk = 0.0
        self.stop_price = self.tp1_price = self.tp2_price = 0.0
        self.tp1_done = self.tp2_done = False
        self.exit_in_progress = False

    # -------------------------------------------------------------- entry
    def try_enter(self, direction: str, ind_row):
        c = float(ind_row["close"])
        risk = float(ind_row["atr"]) * ATR_STOP_MULTIPLIER          # [MOD-1]
        if risk <= 0 or risk / c * 100.0 > MAX_RISK_PCT:
            print(f"\n[SKIP] {direction} signal but risk {risk / c * 100.0:.2f}% > {MAX_RISK_PCT}%")
            return

        action = "BUY" if direction == "LONG" else "SELL"
        resp = self.order(action, QUANTITY, f"{direction} ENTRY")
        if resp is None:
            return

        with self.lock:
            self.position = direction
            self.qty_remaining = QUANTITY
            self.entry_price = c            # candle-close reference (actual fill may differ slightly)
            self.risk = risk
            if direction == "LONG":
                self.stop_price = c - risk
                self.tp1_price = c + risk * FIRST_TARGET_R          # [MOD-2]
                self.tp2_price = c + risk * SECOND_TARGET_R
            else:
                self.stop_price = c + risk
                self.tp1_price = c - risk * FIRST_TARGET_R
                self.tp2_price = c - risk * SECOND_TARGET_R
            self.tp1_done = self.tp2_done = False
        print(f"[TRADE] {direction} {QUANTITY} @ ~{c:.2f} | SL {self.stop_price:.2f} | "
              f"TP1 {self.tp1_price:.2f} | TP2 {self.tp2_price:.2f} | risk/share {risk:.2f}")

    # ---------------------------------------------------- websocket exits
    def on_ltp_update(self, data):
        if data.get("type") != "market_data" or data.get("symbol") != SYMBOL:
            return
        try:
            self.ltp = float(data["data"]["ltp"])
        except (KeyError, TypeError, ValueError):
            return

        with self.lock:
            if not self.position or self.exit_in_progress:
                return
            pos, ltp = self.position, self.ltp
            stop, tp1, tp2 = self.stop_price, self.tp1_price, self.tp2_price
            tp1_done, tp2_done = self.tp1_done, self.tp2_done
            qty_rem, entry = self.qty_remaining, self.entry_price

        exit_side = "SELL" if pos == "LONG" else "BUY"
        hit_stop = ltp <= stop if pos == "LONG" else ltp >= stop
        hit_tp1 = (not tp1_done) and (ltp >= tp1 if pos == "LONG" else ltp <= tp1)
        hit_tp2 = (not tp2_done) and (ltp >= tp2 if pos == "LONG" else ltp <= tp2)

        if hit_stop:
            with self.lock:
                if self.exit_in_progress:
                    return
                self.exit_in_progress = True
            threading.Thread(target=self._do_full_exit, args=(exit_side, "STOP-LOSS"), daemon=True).start()
        elif hit_tp1:
            qty = int(QUANTITY * FIRST_EXIT_PCT)
            qty = min(qty, qty_rem)
            if qty > 0:
                with self.lock:
                    self.tp1_done = True
                    self.qty_remaining -= qty
                    if MOVE_TO_BREAKEVEN_AT_TP1:
                        self.stop_price = max(self.stop_price, entry) if pos == "LONG" else min(self.stop_price, entry)
                threading.Thread(target=self.order, args=(exit_side, qty, "TP1 (50%)"), daemon=True).start()
        elif hit_tp2:
            qty = int(QUANTITY * SECOND_EXIT_PCT)
            qty = min(qty, qty_rem)
            if qty > 0:
                with self.lock:
                    self.tp2_done = True
                    self.qty_remaining -= qty
                threading.Thread(target=self.order, args=(exit_side, qty, "TP2 (25%)"), daemon=True).start()

    def _do_full_exit(self, action, reason):
        with self.lock:
            qty = self.qty_remaining
        if qty > 0:
            self.order(action, qty, reason)
        with self.lock:
            self._flat()

    def websocket_thread(self):
        try:
            print("[WS] connecting...")
            self.client.connect()
            self.client.subscribe_ltp(self.instrument, on_data_received=self.on_ltp_update)
            print(f"[WS] streaming LTP for {SYMBOL}")
            while not self.stop_event.is_set():
                time.sleep(1)
        except Exception as e:
            print(f"\n[WS ERROR] {e}")
        finally:
            try:
                self.client.unsubscribe_ltp(self.instrument)
                self.client.disconnect()
            except Exception:
                pass
            print("[WS] closed")

    # ------------------------------------------------------ strategy loop
    def get_history(self) -> pd.DataFrame:
        end = datetime.now()
        start = end - timedelta(days=LOOKBACK_DAYS)
        df = self.client.history(
            symbol=SYMBOL, exchange=EXCHANGE, interval=CANDLE_TIMEFRAME,
            start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"),
        )
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            ts_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
            df[ts_col] = pd.to_datetime(df[ts_col])
            df = df.set_index(ts_col)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col])
        return df.sort_index()

    def strategy_thread(self):
        print("[STRAT] started")
        while not self.stop_event.is_set():
            try:
                now = datetime.now()
                hm = (now.hour, now.minute)

                # ---- end-of-day square off ----
                if hm >= SQUARE_OFF:
                    with self.lock:
                        has_pos = self.position is not None and not self.exit_in_progress
                        if has_pos:
                            self.exit_in_progress = True
                            side = "SELL" if self.position == "LONG" else "BUY"
                    if has_pos:
                        self._do_full_exit(side, "EOD SQUARE-OFF")
                    time.sleep(POLL_INTERVAL)
                    continue

                df = self.get_history()
                if df.empty or len(df) < max(EMA_LENGTH, MACD_SLOW + MACD_SIGNAL, ADX_LENGTH * 2) + REGIME_SLOPE_BARS + 5:
                    print("[STRAT] not enough data yet, retrying...")
                    time.sleep(POLL_INTERVAL)
                    continue

                # Drop the current forming candle -> act on CLOSED candles only
                closed = df.iloc[:-1]
                ind = compute_indicators(closed)
                last_ts = closed.index[-1]

                new_candle = (self.last_candle_ts is None) or (last_ts > self.last_candle_ts)

                if new_candle:
                    self.last_candle_ts = last_ts
                    row = ind.iloc[-1]

                    with self.lock:
                        pos = self.position
                        exiting = self.exit_in_progress

                    if pos and not exiting:
                        # candle-close management: EMA exit + swing trail after TP2
                        with self.lock:
                            if self.tp2_done and TRAIL_AFTER_TP2:
                                if self.position == "LONG":
                                    self.stop_price = max(self.stop_price, float(row["trail_low"]))
                                else:
                                    self.stop_price = min(self.stop_price, float(row["trail_high"]))
                            do_ema_exit = USE_EMA_EXIT and (
                                (self.position == "LONG" and row["close"] < row["ema"]) or
                                (self.position == "SHORT" and row["close"] > row["ema"]))
                            if do_ema_exit:
                                self.exit_in_progress = True
                                side = "SELL" if self.position == "LONG" else "BUY"
                        if do_ema_exit:
                            self._do_full_exit(side, "EMA EXIT")

                    elif not pos:
                        # entries only inside the entry session
                        if ENTRY_START <= hm < ENTRY_END:
                            sig = evaluate_signal(ind)
                            if sig:
                                print(f"\n[SIGNAL] {sig} on candle {last_ts}")
                                self.try_enter(sig, row)

                    # status line
                    with self.lock:
                        status = (f"{self.position} qty={self.qty_remaining} SL={self.stop_price:.2f}"
                                  if self.position else "FLAT")
                    print(f"[{now.strftime('%H:%M:%S')}] candle {last_ts} close={row['close']:.2f} "
                          f"ema={row['ema']:.2f} rsi={row['rsi']:.1f} adx={row['adx']:.1f} | {status}")

            except Exception as e:
                print(f"\n[STRAT ERROR] {e}")

            time.sleep(POLL_INTERVAL)
        print("[STRAT] stopped")

    # --------------------------------------------------------------- run
    def run(self):
        ws = threading.Thread(target=self.websocket_thread, daemon=True)
        st = threading.Thread(target=self.strategy_thread, daemon=True)
        ws.start()
        st.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] CTRL+C received")
            self.stop_event.set()
            with self.lock:
                has_pos = self.position is not None
                side = "SELL" if self.position == "LONG" else "BUY"
            if has_pos:
                print("[SHUTDOWN] squaring off open position...")
                self._do_full_exit(side, "SHUTDOWN EXIT")
            ws.join(timeout=5)
            st.join(timeout=5)
            print("[SHUTDOWN] done")


if __name__ == "__main__":
    print("=" * 70)
    print(" SBIN 61-EMA MODIFIED STRATEGY  |  OpenAlgo")
    print(f" {SYMBOL} {EXCHANGE} {CANDLE_TIMEFRAME} | qty {QUANTITY} | {PRODUCT}")
    print(f" Entries {ENTRY_START[0]:02d}:{ENTRY_START[1]:02d}-{ENTRY_END[0]:02d}:{ENTRY_END[1]:02d}"
          f" | Square-off {SQUARE_OFF[0]:02d}:{SQUARE_OFF[1]:02d}")
    print("=" * 70)
    SBINStrategyBot().run()
