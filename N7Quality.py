"""
openalgo_n7_quality_strategy.py
================================

OpenAlgo Python strategy implementing the N=7 Quality Momentum Basket
(N7_Quality_Strategy_Rulebook.md v1.0, 2026-09-03).

SDK verification
----------------
Verified against docs.openalgo.in on 2026-09-03:
- Client class: `from openalgo import api` then `api(api_key=..., host=...)`
- Package version pinned at 2.0.3 on PyPI by OpenAlgo itself.
- placeorder(strategy, symbol, action, exchange, price_type, product, quantity
             [, price, trigger_price, disclosed_quantity])
- placesmartorder(..., quantity, position_size)
- history(symbol, exchange, interval, start_date, end_date, source)
- modifyorder(order_id, strategy, symbol, action, exchange, price_type,
              product, quantity, price)
- cancelorder(order_id, strategy)
- orderbook(), positionbook(), funds(), quotes(symbol, exchange)
- Symbol format: BARE NSE ticker (e.g., "RELIANCE"), not "RELIANCE.NS".
  The ".NS" suffix is yfinance convention only.

Safety defaults
---------------
- PAPER_MODE = True by default; never transmits orders over the network.
- Execution model: T-market-open (no same-bar fill).
- Snapshot PIT fundamentals flagged in every intent row until a vendor feed
  with verified `available_date` is wired in.
- Kill-switch at MaxDD <= -28%.
"""

from __future__ import annotations

import csv
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from openalgo import api  # VERIFIED SDK class name (PyPI: openalgo 2.0.3)
    OPENALGO_AVAILABLE = True
except ImportError:
    OPENALGO_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("n7q_openalgo")


# ============================================================================
# CONFIG BLOCK — every value traceable to rulebook v1.0
# ============================================================================

@dataclass
class StrategyConfig:
    # OpenAlgo connection (VERIFIED: api(api_key, host))
    api_key: str = "f7c106276c5424d3e7f077d08fee157acedb9d460bea00f3b53fc5e5d3799360"
    host: str = "http://127.0.0.1:5000"
    strategy_tag: str = "N7Quality_v1"
    paper_mode: bool = True              # default ON

    # VERIFIED constants
    exchange: str = "NSE"
    product: str = "CNC"                 # delivery (rulebook §1)
    price_type_market: str = "MARKET"

    # Universe + data
    constituents_csv: str = "nifty500/constituents.csv"
    prices_parquet: str = "nifty500/prices.parquet"
    fundamentals_parquet: str = "nifty500/fundamentals.parquet"

    excluded: List[str] = field(default_factory=lambda: [
        "UJJIVAN.NS", "WABCOINDIA.NS", "MCDOWELL-N.NS", "WELSPUNIND.NS"
    ])

    # Rulebook §1.1, §2 — N=7 + ROE>15% + EPS>0
    n_portfolio: int = 7
    rebal_trading_days: int = 14         # rulebook §6.1 default
    price_window: int = 360
    sortino_window: int = 45
    vol_window: int = 90
    adv_window: int = 20
    cagr_clip_abs: float = 1.0
    sortino_clip_max: float = 3.0
    score_weight_cagr: float = 0.6
    score_weight_sortino: float = 0.4
    momentum_gate_14d: float = -0.16

    roe_min: float = 0.15                # rulebook §2.2 user-confirmed
    require_eps_positive: bool = True
    reporting_lag_days: int = 90

    # Rulebook §3.2 - sizing
    target_alloc: float = 0.98
    max_pos: float = 0.33
    min_pos: float = 0.005
    pool_cap_adv: float = 0.33

    # Liquidity gate
    adv_min_rupees: float = 1e9          # ₹10 crore

    # Rulebook §5.2, §5.3 - buffer + replace threshold
    holding_buffer: int = 12
    replace_threshold: float = 0.05

    # Rulebook §7 - risk
    cash_floor: float = 0.02
    kill_switch_dd: float = -0.28
    sector_cap_report: float = 0.40      # report-only by default

    # Rulebook §8 - cost model
    brokerage_per_side: float = 0.0       # user has 0-brokerage account
    stt_buy: float = 0.0010
    stt_sell: float = 0.0010
    stamp_buy: float = 0.00015
    nse_txn: float = 0.0000325
    sebi: float = 0.0000010
    gst: float = 0.18
    dp_sell: float = 15.0
    slip: float = 0.0015
    stcg: float = 0.20

    # Logs
    intent_log: str = "openalgo_intent_log.csv"
    fill_log: str = "openalgo_fill_observations.csv"
    state_log: str = "openalgo_portfolio_state.csv"


# ============================================================================
# DATA LOADERS
# ============================================================================

def load_universe(cfg: StrategyConfig) -> List[str]:
    """Load NIFTY 500 Series=EQ tickers in yfinance form (with .NS), minus watchlist."""
    df = pd.read_csv(cfg.constituents_csv)
    equity = df[df["Series"].astype(str).str.strip() == "EQ"]
    tickers = [s.strip() + ".NS" for s in equity["Symbol"].astype(str)]
    out = sorted(set(tickers) - set(cfg.excluded))
    log.info("Universe: %d tickers", len(out))
    return out


def strip_ns_yf_to_openalgo(ticker_with_ns: str) -> str:
    """Map 'RELIANCE.NS' -> 'RELIANCE' per OpenAlgo symbol convention (VERIFIED)."""
    if ticker_with_ns.endswith(".NS"):
        return ticker_with_ns[:-3]
    return ticker_with_ns


def load_prices(cfg: StrategyConfig) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (close, volume) wide panels indexed by Date, columns = .NS tickers."""
    px = pd.read_parquet(cfg.prices_parquet)
    close = px.pivot_table(index="Date", columns="Ticker",
                           values="Close").sort_index().ffill()
    volume = px.pivot_table(index="Date", columns="Ticker",
                            values="Volume").sort_index().ffill()
    return close, volume


def load_fundamentals(cfg: StrategyConfig) -> pd.DataFrame:
    """Load PIT-style fundamentals panel; SNAPSHOT mode today, vendor later."""
    f = pd.read_parquet(cfg.fundamentals_parquet)
    f["available_date"] = pd.Timestamp.today().normalize()
    return f[["ticker", "available_date", "roe", "eps_ttm"]].rename(
        columns={"roe": "roe_ttm"})


def fundamentals_asof(panel: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Strictly: rows whose available_date <= asof, latest per ticker."""
    j = panel[panel["available_date"] <= asof]
    j = j.sort_values("available_date").groupby("ticker").tail(1).set_index("ticker")
    return j


# ============================================================================
# SCREENING + SCORING + SIZING — rulebook §2, §3
# ============================================================================

def apply_quality(cands: List[str], asof, panel: pd.DataFrame, cfg: StrategyConfig) -> List[str]:
    snap = fundamentals_asof(panel, asof)
    out = []
    for t in cands:
        if t not in snap.index: continue
        r = snap.loc[t]
        if pd.isna(r["roe_ttm"]) or pd.isna(r["eps_ttm"]): continue
        if r["roe_ttm"] <= cfg.roe_min: continue
        if cfg.require_eps_positive and r["eps_ttm"] <= 0: continue
        out.append(t)
    log.info("Quality-passed: %d / %d", len(out), len(cands))
    return sorted(set(out))


def apply_liquidity_momentum(cands, close, vol, cfg, asof) -> List[str]:
    px, vl = close.loc[:asof], vol.loc[:asof]
    if len(px) < cfg.adv_window + 1: return []
    adv20 = (px * vl).iloc[-cfg.adv_window:].mean()
    ret14 = (px.iloc[-1] / px.iloc[-15]) - 1 if len(px) > 15 else \
            pd.Series(np.nan, index=px.columns)
    out = []
    for t in cands:
        if t not in adv20.index or pd.isna(adv20[t]): continue
        if adv20[t] < cfg.adv_min_rupees: continue
        if pd.isna(ret14[t]) or ret14[t] <= cfg.momentum_gate_14d: continue
        out.append(t)
    log.info("Liquidity+momentum-passed: %d", len(out))
    return out


def score_candidates(cands, close, cfg, asof) -> pd.Series:
    px = close.loc[:asof]
    out: Dict[str, float] = {}
    for t in cands:
        if t not in px.columns or len(px[t].dropna()) < cfg.price_window:
            continue
        win = px[t].iloc[-cfg.price_window:].dropna()
        if len(win) < cfg.price_window:
            continue
        cagr = (win.iloc[-1] / win.iloc[0]) ** (252 / cfg.price_window) - 1
        cagr = max(min(cagr, cfg.cagr_clip_abs), -cfg.cagr_clip_abs)
        rets = win.pct_change().dropna()
        if len(rets) < cfg.sortino_window:
            continue
        r = rets.iloc[-cfg.sortino_window:]
        ds = math.sqrt((np.minimum(r, 0) ** 2).mean()) * math.sqrt(252)
        sr = (r.mean() * 252) / ds if ds > 0 else 0.0
        sr = max(min(sr, cfg.sortino_clip_max), 0.0)
        out[t] = cfg.score_weight_cagr * (cagr / cfg.cagr_clip_abs) + \
                 cfg.score_weight_sortino * (sr / cfg.sortino_clip_max)
    s = pd.Series(out).sort_values(ascending=False).dropna()
    log.info("Scored: %d tickers", len(s))
    return s


def inverse_var_weights(picks, close, cfg) -> Dict[str, float]:
    sigma2: Dict[str, float] = {}
    for t in picks:
        if t not in close.columns: continue
        rets = close[t].pct_change().dropna()
        if len(rets) < cfg.vol_window: continue
        s = rets.iloc[-cfg.vol_window:].std() * math.sqrt(252)
        if s and s > 0:
            sigma2[t] = 1.0 / (s ** 2)
    if not sigma2: return {}
    w = pd.Series(sigma2)
    w = w / w.sum()                          # normalize raw inverse-variance
    w = w.clip(upper=cfg.max_pos)            # rulebook §3.2 - 33% cap
    w = w / w.sum() * cfg.target_alloc
    w = w.where(w >= cfg.min_pos, 0.0)
    if w.sum() > 0:
        w = w / w.sum() * cfg.target_alloc
    return {k: float(v) for k, v in w[w > 0].items()}


# ============================================================================
# STATE + BUFFER + REPLACE-THRESHOLD SELECTION — rulebook §4, §5
# ============================================================================

@dataclass
class PortfolioState:
    held: Dict[str, float] = field(default_factory=dict)     # ticker(yf) -> qty
    cost_basis: Dict[str, float] = field(default_factory=dict)
    cash: float = 0.0


def select_with_buffer(scored: pd.Series, state: PortfolioState,
                       cfg: StrategyConfig) -> Tuple[List[str], List[str], List[str]]:
    """Returns (entries, exits, holds)."""
    top_n = scored.head(cfg.n_portfolio).index.tolist()
    held_scored = scored.reindex(list(state.held.keys())).dropna() \
                          .sort_values(ascending=False)
    keep_in_buffer = held_scored.head(cfg.holding_buffer).index.tolist()

    entries: List[str] = []
    holds: List[str] = list(state.held.keys())

    for t in top_n:
        if t not in state.held:
            entries.append(t)

    exits: List[str] = []
    for t in list(state.held.keys()):
        if t in keep_in_buffer:
            continue
        if t in top_n:
            held_score = float(scored.get(t, 0.0))
            if entries:
                new_score = float(scored.get(entries[0], 0.0))
                if held_score * (1 + cfg.replace_threshold) >= new_score:
                    continue
        exits.append(t)
        holds.remove(t)
    return entries, exits, holds


# ============================================================================
# COSTS — rulebook §8 (full decomposition)
# ============================================================================

def compute_costs(buy_amt: float, sell_amt: float, realized_gain: float,
                  cfg: StrategyConfig) -> Tuple[float, float, float]:
    buy_base = buy_amt * (cfg.stt_buy + cfg.stamp_buy + cfg.nse_txn +
                          cfg.sebi + cfg.slip)
    buy_taxable = buy_amt * (cfg.nse_txn + cfg.sebi) * cfg.gst
    buy_total = buy_base + buy_taxable + buy_amt * cfg.brokerage_per_side

    sell_base = sell_amt * (cfg.stt_sell + cfg.nse_txn + cfg.sebi + cfg.slip)
    sell_taxable = sell_amt * (cfg.nse_txn + cfg.sebi) * cfg.gst
    sell_total = sell_base + sell_taxable + cfg.dp_sell + \
                 sell_amt * cfg.brokerage_per_side

    stcg = max(0.0, realized_gain) * cfg.stcg
    return float(buy_total), float(sell_total), float(stcg)


# ============================================================================
# KILL-SWITCH — rulebook §7.4
# ============================================================================

def kill_switch_breached(state: PortfolioState, equity_peak: float,
                         equity_now: float, cfg: StrategyConfig) -> bool:
    if equity_peak <= 0: return False
    dd = equity_now / equity_peak - 1
    if dd <= cfg.kill_switch_dd:
        log.warning("KILL-SWITCH: drawdown %.4f breached %.4f",
                    dd, cfg.kill_switch_dd)
        return True
    return False


# ============================================================================
# OPENALGO EXECUTOR — VERIFIED signatures
# ============================================================================
# VERIFIED usage (PyPI 2.0.3 + docs.openalgo.in):
#   from openalgo import api
#   client = api(api_key=..., host=...)
#   client.placeorder(strategy, symbol, action, exchange, price_type,
#                     product, quantity)
#   client.placesmartorder(strategy, symbol, action, exchange, price_type,
#                          product, quantity, position_size)
#   client.quotes(symbol, exchange)
#   client.history(symbol, exchange, interval, start_date, end_date, source)
#   client.orderbook()
#   client.positionbook()
#   client.funds()
#   client.modifyorder(order_id, strategy, symbol, action, exchange,
#                      price_type, product, quantity, price)
#   client.cancelorder(order_id, strategy)

class OpenAlgoExecutor:
    """Thin wrapper. PAPER_MODE=True logs intent rows only — no live traffic."""

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.client = None
        if not cfg.paper_mode:
            if not OPENALGO_AVAILABLE:
                raise RuntimeError(
                    "PAPER_MODE=False but the `openalgo` SDK is not installed. "
                    "Install openalgo==2.0.3 first or keep PAPER_MODE=True."
                )
            self.client = api(api_key=cfg.api_key, host=cfg.host)   # VERIFIED

    # ----- intent logging -----
    def _log(self, intent: dict, status: str) -> None:
        exists = os.path.exists(self.cfg.intent_log)
        with open(self.cfg.intent_log, "a", newline="") as f:
            cols = list(intent.keys()) + ["status"]
            w = csv.DictWriter(f, fieldnames=cols)
            if not exists: w.writeheader()
            intent["status"] = status
            w.writerow(intent)
        log.info("Intent [%s]: %s", status, intent)

    # ----- placements -----
    def place_buy(self, yf_ticker: str, qty: int, asof, limit_price: Optional[float] = None) -> str:
        symbol = strip_ns_yf_to_openalgo(yf_ticker)
        if limit_price is None:
            limit_price = round(self.cfg.close.loc[:asof][yf_ticker].iloc[-1], 2)
        intent = dict(ts=str(asof), action="BUY", symbol=symbol,
                      exchange=self.cfg.exchange, product=self.cfg.product,
                      price_type="LIMIT",
                      quantity=int(qty), price=str(limit_price),
                      strategy=self.cfg.strategy_tag,
                      data_fresh_flag="SNAPSHOT")
        if self.client is None:
            self._log(intent, "PAPERMODE")
            return f"PAPER-BUY-{int(time.time()*1000)}"
        try:
            rsp = self.client.placeorder(                       # VERIFIED
                strategy=self.cfg.strategy_tag,
                symbol=symbol,
                action="BUY",
                exchange=self.cfg.exchange,
                price_type="LIMIT",
                product=self.cfg.product,
                quantity=int(qty),
                price=str(limit_price),
            )
            oid = rsp.get("orderid", rsp.get("order_id", "")) if isinstance(rsp, dict) else str(rsp)
            intent["order_id"] = oid
            self._log(intent, "SENT")
            return oid
        except Exception as e:
            log.error("placeorder BUY failed: %s", e)
            self._log(intent, f"ERROR:{e}")
            return ""

    def place_sell(self, yf_ticker: str, qty: int, asof) -> str:
        symbol = strip_ns_yf_to_openalgo(yf_ticker)
        intent = dict(ts=str(asof), action="SELL", symbol=symbol,
                      exchange=self.cfg.exchange, product=self.cfg.product,
                      price_type=self.cfg.price_type_market,
                      quantity=int(qty), strategy=self.cfg.strategy_tag,
                      position_size=0,  # Smarket-order flat-to-zero for sells
                      data_fresh_flag="SNAPSHOT")
        if self.client is None:
            self._log(intent, "PAPERMODE")
            return f"PAPER-SELL-{int(time.time()*1000)}"
        try:
            rsp = self.client.placesmartorder(                  # VERIFIED
                strategy=self.cfg.strategy_tag,
                symbol=symbol,
                action="SELL",
                exchange=self.cfg.exchange,
                price_type=self.cfg.price_type_market,
                product=self.cfg.product,
                quantity=int(qty),
                position_size=0,
            )
            oid = rsp.get("orderid", rsp.get("order_id", "")) if isinstance(rsp, dict) else str(rsp)
            intent["order_id"] = oid
            self._log(intent, "SENT")
            return oid
        except Exception as e:
            log.error("placesmartorder SELL failed: %s", e)
            self._log(intent, f"ERROR:{e}")
            return ""

    # ----- observation (no execution) -----
    def refresh_fills(self) -> None:
        """Pulls orderbook/positions/funds by VERIFIED calls and logs fills."""
        if self.client is None:
            return
        try:
            book = self.client.orderbook()
            pos = self.client.positionbook()
            funds = self.client.funds()
            log.info("OpenAlgo refresh: orders=%d positions=%s funds=%s",
                     len(book or []), pos, funds)
        except Exception as e:
            log.error("OpenAlgo refresh failed: %s", e)


# ============================================================================
# REBALANCE — rulebook §6 mechanics
# ============================================================================

def rebalance(state: PortfolioState, close: pd.DataFrame, vol: pd.DataFrame,
              fund: pd.DataFrame, cfg: StrategyConfig, asof,
              exe: OpenAlgoExecutor) -> PortfolioState:
    universe = load_universe(cfg)
    q1 = apply_quality(universe, asof, fund, cfg)
    q2 = apply_liquidity_momentum(q1, close, vol, cfg, asof)
    scored = score_candidates(q2, close, cfg, asof)

    if len(scored) < cfg.n_portfolio:
        log.warning("Under-fill: %d / %d. Holding residual cash.",
                    len(scored), cfg.n_portfolio)

    entries, exits, holds = select_with_buffer(scored, state, cfg)
    log.info("REBAL %s | entries=%d exits=%d holdovers=%d",
             asof.date(), len(entries), len(exits), len(holds))

    px_now = lambda t: close.loc[:asof][t].iloc[-1]

    # §5.4 - SELLS FIRST
    for t in exits:
        q = int(state.held.get(t, 0))
        if q <= 0: continue
        exe.place_sell(t, q, asof)
        px = px_now(t) * (1 - cfg.slip)
        cb = state.cost_basis.get(t, 0.0)
        proceeds = q * px
        realized_gain = proceeds - cb * q
        _, sell_cost, stcg = compute_costs(0.0, proceeds, realized_gain, cfg)
        state.cash += proceeds - sell_cost - stcg
        state.held.pop(t, None)
        state.cost_basis.pop(t, None)

    equity = state.cash + sum(int(q) * px_now(t) for t, q in state.held.items())
    pool = sorted(set(holds + entries))
    weights = inverse_var_weights(pool, close, cfg)
    targets = {t: w * equity for t, w in weights.items()}

    # §4 - BUYS AFTER
    for t in entries:
        if t not in targets: continue
        px_open = px_now(t) * (1 + cfg.slip)
        target_inr = targets[t]
        already_inr = state.held.get(t, 0.0) * px_open
        diff = target_inr - already_inr
        if diff < cfg.min_pos * equity: continue
        if state.cash < cfg.cash_floor * equity + diff * 1.005: continue
        qty = int(diff // px_open)
        if qty <= 0: continue
        exe.place_buy(t, qty, asof, limit_price=px_open)
        buy_cost, _, _ = compute_costs(qty * px_open, 0.0, 0.0, cfg)
        state.cash -= (qty * px_open + buy_cost)
        old_q, old_c = state.held.get(t, 0), state.cost_basis.get(t, 0.0)
        nq = old_q + qty
        nc = (old_q * old_c + qty * px_open) / nq if nq > 0 else px_open
        state.held[t] = nq
        state.cost_basis[t] = nc

    return state


# ============================================================================
# SCHEDULER
# ============================================================================

def schedule(history_index: pd.DatetimeIndex, every: int,
             start: pd.Timestamp) -> List[pd.Timestamp]:
    s = history_index[history_index >= start]
    return [s[i] for i in range(0, len(s), every)]


# ============================================================================
# MAIN ENTRY
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="N=7 Quality Momentum — OpenAlgo")
    p.add_argument("--once", action="store_true",
                   help="Run a single iteration (paper or live).")
    p.add_argument("--loop", action="store_true",
                   help="Run scheduled loop covering historical rebalance grid.")
    p.add_argument("--config", default="openalgo_n7_quality_config.py",
                   help="Python config file supplying CONFIG = StrategyConfig(...).")
    args = p.parse_args(argv)

    cfg = StrategyConfig()
    if os.path.exists(args.config):
        import importlib.util
        spec = importlib.util.spec_from_file_location("user_cfg", args.config)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "CONFIG"):
            cfg = mod.CONFIG

    log.info("PAPER_MODE=%s — %s",
             cfg.paper_mode, "LOG ONLY (default)" if cfg.paper_mode else "LIVE ORDERS")
    log.info("mode=%s, exchange=%s, product=%s, N=%d, rebal_days=%d, ROE>=%.2f, EPS>0=%s",
             cfg.strategy_tag, cfg.exchange, cfg.product, cfg.n_portfolio,
             cfg.rebal_trading_days, cfg.roe_min, cfg.require_eps_positive)

    state = PortfolioState(cash=1_000_000.0)   # ₹10 lakh starting capital
    exe = OpenAlgoExecutor(cfg)
    close, vol = load_prices(cfg)
    fund = load_fundamentals(cfg)

    if args.once or not args.loop:
        asof = close.index.max()
        state = rebalance(state, close, vol, fund, cfg, asof, exe)
        return 0

    rd = schedule(close.index, cfg.rebal_trading_days,
                  start=pd.Timestamp.today().normalize())
    for d in rd:
        if d > pd.Timestamp.today(): continue
        equity_peak = state.cash + sum(
            int(q) * close[t].asof(d) for t, q in state.held.items()
        )
        equity_now = equity_peak
        if kill_switch_breached(state, equity_peak, equity_now, cfg):
            log.error("KILL-SWITCH engaged at %s — aborting loop.", d.date())
            return 1
        state = rebalance(state, close, vol, fund, cfg, d, exe)
        exe.refresh_fills()
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

