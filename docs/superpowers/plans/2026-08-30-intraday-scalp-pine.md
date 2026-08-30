# Intraday Scalp Strategy (1m DuckDB → Pine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discovery-driven, evidence-validated intraday mean-reversion scalp strategy on NIFTY 500 1-minute bars, delivered as a TradingView Pine `strategy()` script plus a Python mirror (vectorbt on DuckDB) that reproduces identical results.

**Architecture:** A research pipeline with three layers: (1) data hygiene producing canonical clean per-symbol 1m series; (2) discovery + selection (signal library → forward-return edge maps → per-day symbol selector); (3) validated strategy spec (mean-reversion entry + breakeven/trailing runner exits) expressed in one Python backtest engine, then hand-ported to Pine and cross-verified for trade-for-trade parity.

**Tech Stack:** Python 3.12 (`.venv` in repo), duckdb, pandas, vectorbt, plotly, numpy, ruff; TradingView Pine Script v5.

**Spec:** This plan is self-contained (approved design from 2026-08-30 brainstorming; no separate spec file exists). All Global Constraints below are normative.

## Global Constraints

- **Environment:** Never use global Python/`pip`. Always `uv run` or `./.venv/bin/python`. Python interpreter is `.venv/bin/python` (repo-local).
- **Source data:** `db/historify.duckdb`, table `market_data` — read-only. Never mutate it.
- **Clean data rule:** Remove the 27,600 spurious bars at 18:00–19:00 IST on **2024-11-01 / 2024-11-02** (460 symbols). These are the only intra-day rows outside 09:15–15:30 IST (confirmed by hour histogram: 09,10,11,12,13,14,15,18).
- **Partial-day rule:** 2025-10-21 has a single 13:45–14:45 block (61 bars) for 488 symbols. Full-session backtests must exclude or down-weight this date; when comparing sessions, flag it.
- **Late-first-bar rule:** Session start = first available bar each day; never assume a 09:15:00 print exists; never compute signals before the first bar.
- **Bar timestamp convention:** timestamps are Unix epoch seconds of the **bar-open minute, IST** (09:15:00 = first bar). Pine bar-time truncation must match (see Pine port task).
- **Cost model (normative):** 0.1% per side = **0.001** per side applied to both entry and exit notional (all-in: brokerage + STT/charges + slippage). Round trip = 0.002. Model as vectorbt `fees=0.001` with fixed `fixed_fees=0`, AND as an explicit flat 0.001 on turn value in any hand-rolled calc.
- **Profitability bar:** net (after-cost) positive expectancy, **profit factor ≥ 1.3**, verified in **walk-forward out-of-sample**; **≥ 500 net OOS trades**; per-day and per-symbol consistency required for the PASS verdict.
- **Kill criteria (honesty gate):** If no candidate clears the bar in OOS, the final report delivers the negative result + nearest candidates + diagnosis. Do NOT relabel a failing strategy as profitable.
- **No lookahead:** Signals computed on the CLOSE of bar `k`; entries/orders simulated at the OPEN of bar `k+1` (or `process_orders_on_close` semantics in Pine, matching mirror). Symbol selection uses prior-day/pre-open data only.
- **Determinism:** Pine and Python mirror must produce the same trade list/equity on identical bars within open-vs-close-of-bar timing tolerance. Mismatch = bug, not tolerated.
- **Session:** Trading session 09:15–15:30 IST; hard exit 15:15 IST by default (configurable in config.py).
- **Liquidity gate:** a symbol is "tradable" if median daily notional ≥ **₹10 Cr** over the train window (computed from `volume × close`). Selector and backtest operate only on tradable symbols; untradable names are excluded from trade candidates but still used for nothing downstream.
- **Fees constants:** the repo's `quick-stats` skill uses delivery fees (0.111% + ₹20). This scalp project uses the intraday model in this plan (0.001/side flat). Do not mix.
- **Repo conventions:** Use `logger.exception()` for errors, no `traceback` imports. Ruff lint+format before finishing. No comments unless needed. Follow quick-stats style (Plotly dark, compact stats block) for reports.

## File Structure

```
backtesting/scalpel/
  config.py               # costs, session, profitability/pf/trade bars, liquidity gate, paths
  hygiene.py              # clean_1m_series() : duckdb query -> sanitized DataFrame; anomaly report fn
  tests/test_hygiene.py   # purity tests (no 18:xx, no 2025-10-21, session bounds)
  loader.py               # day_series(), prev_day_features(), notional_medians() cached to backtesting/scalpel/cache/
  signals.py              # scalar signal builder: vwap_z, rsi2, bbpct, roll_z, or_fade, rvol
  tests/test_signals.py   # vectorized correctness tests (degenerate inputs, lookahead guard)
  edge_map.py             # edge_expectancy() : conditioning grid -> expectancy/edge-maps (parquet + CSV)
  selector.py             # rank_symbols(date_info) -> top-N armed daily candidates (uses prior-day features)
  engine.py               # run_strategy_bars(bars, params) -> vectorbt Portfolio log (entry/exit/trail/costs)
  tests/test_engine.py    # single-trade scenarios: winner->breakeven->trail, stop hit, EOD exit, cost deduction
  walkforward.py          # walk-forward folds, robustness tolerance grid, sufficiency counters -> report dict
  report.py               # stats block + Plotly equity/cum-return charts -> backtesting/scalpel/results/
  verify_pine_mirror.py   # Pine vs mirror trade/equity parity checker on 3 symbols x 6 months
  pine/scalpel_intraday.pine   # the Pine strategy
  pine/scalpel_screener.pine   # Pine overlay screener (armed-score), non-strategy
  cache/                  # cached prev-day features + notional medians (gitignored)
  study/                  # discovery edge-map outputs (markdown + CSV + png)
  results/                # walk-forward + verification reports (markdown + png)
```

## Interfaces (fixed before implementation)

- `hygiene.clean_1m_series(con, symbol, start_epoch, end_epoch) -> pd.DataFrame` with columns `[timestamp(epoch,int), datetime(ist,tz-naive), symbol, open, high, low, close, volume, oi]`, sorted ascending, anomalies removed.
- `loader.prev_day_features(con, date_ist) -> pd.DataFrame` one row per tradable symbol: `{symbol, prev_gap_pct, prev_trend_pct(20d), prev_vol_pct, prev_rvol_median, prev_notional_median, prev_ret_5m, prev_close_to_high}`.
- `loader.day_series(con, symbol, date_ist) -> pd.DataFrame` (sub-day frame of clean_1m_series).
- `signals.build_frame(df, params) -> dict[str, np.ndarray]` returns arrays indexed by bar: `vwap_z, rsi2, bbpct, roll_z, or_fade, rvol, atr5 (numeric), trend_up (bool)`. All computed only at/after first bar; `NaN`-safe.
- `edge_map.edge_expectancy(frames, params, cost) -> dict[str, pd.DataFrame]` keyed by signal; DF rows = conditioning buckets, columns = `{n, mean_fwd_ret, hit_rate, net_exp, pf_approx}` for hold h in {1,3,5,10,15} min.
- `selector.rank_symbols(prev_features, date_info, n_top=3) -> list[str]` deterministic ranking, explicit rule string in output for the Pine screener.
- `engine.run_strategy(bars, params, cost=0.001) -> Analysis` object with `.trades` (DataFrame: entry_ts/side/entry/exit/sl_events/r/ret), `.pf`, `.expectancy`, `.n_trades`, `.equity` (cum).
- `walkforward.walk_forward(frames_by_symbol, params_grid) -> dict` with `{folds:[...], oos_trades, oos_pf, oos_expectancy, robustness:{}, sufficiency:{}}`.
- Pine mirrors the same numeric params as `engine.run_strategy`; names matched in `verify_pine_mirror.py`.

---

### Task 1: Workspace scaffold + config + hygiene

**Files:**
- Create: `backtesting/scalpel/config.py`
- Create: `backtesting/scalpel/hygiene.py`
- Create: `backtesting/scalpel/tests/test_hygiene.py`
- Create: `backtesting/scalpel/cache/.gitkeep`

**Interfaces:**
- Produces: `config.COST_PER_SIDE=0.001`, `config.SESSION_START="09:15"`, `config.SESSION_END="15:30"`, `config.HARD_EXIT="15:15"`, `config.LIQUIDITY_GATE_CR=10.0`, `config.KILL_PF=1.3`, `config.KILL_MIN_OOS_TRADES=500`, `config.BAD_DAYS={"2025-10-21"}`, `config.PINE_DIR`, data paths. `hygiene.clean_1m_series(con, symbol, start_epoch, end_epoch)`.

- [ ] **Step 1: Write failing tests**

`test_hygiene.py`:

```python
import duckdb, pytest
from dummy_con import FakeCon  # fixture returning canned rows
# Use a real duckdb in-memory DB seeded with the anomaly rows + a valid day:
#  (a) rows at 2024-11-01 18:00, 2024-11-02 18:30 must be absent from output
#  (b) 2025-10-21 rows present but flagable; series has no rows outside 09:15-15:30 IST
#  (c) output sorted ascending, columns exact, no NaN in OHLC
```

- [ ] **Step 2: Run tests, expect FAIL (module missing)**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_hygiene.py -v`

- [ ] **Step 3: Implement `config.py` and `hygiene.py`**

`hygiene.clean_1m_series` executes one read-only query on `market_data`:

```sql
SELECT "timestamp", open, high, low, close, volume, oi
FROM market_data
WHERE symbol = ? AND "timestamp" BETWEEN ? AND ?
  AND NOT (strftime('%H', to_timestamp("timestamp") + INTERVAL '5.5 hours') = '18')
ORDER BY "timestamp";
```

then in pandas: drop rows whose IST local time (`utc + 5:30`, tz-naive) is outside 09:15–15:30; flag date 2025-10-21 via `config.BAD_DAYS`; drop rows with `high<low` or any of open/high/low/close ≤ 0; sort; return frame with `datetime` column added. Add `hygiene.anomaly_report(con)` printing counts of excluded holiday bars/bad-day rows for the audit.

- [ ] **Step 4: Run tests, expect PASS**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_hygiene.py -v`

- [ ] **Step 5: Smoke-verify against real DB**

Run: `./.venv/bin/python -c "import duckdb; from backtesting.scalpel import hygiene; c=duckdb.connect('db/historify.duckdb',read_only=True); df=hygiene.clean_1m_series(c,'HDFCBANK',1723779900,1786701540); print(len(df), df.datetime.min(), df.datetime.max(), (df.datetime.dt.hour==18).sum())"`
Expected: ~185k rows, min 2024-08-16 09:15:00, max 2026-08-14 15:29:00, zero 18:xx rows.

- [ ] **Step 6: Commit**

```bash
git add backtesting/scalpel
git commit -m "feat(scalpel): workspace scaffold, config, 1m hygiene"
```

---

### Task 2: loader (day series + prev-day features + liquidity)

**Files:**
- Create: `backtesting/scalpel/loader.py`
- Create: `backtesting/scalpel/tests/test_loader.py`

**Interfaces:**
- Consumes: `hygiene.clean_1m_series`, `config`.
- Produces: `loader.day_series(con, symbol, date_ist) -> pd.DataFrame`; `loader.prev_day_features(con, date_ist) -> pd.DataFrame`; `loader.notional_medians(con, start_epoch, end_epoch) -> dict[str,float]` (cached to `cache/notional_medians.parquet`).

- [ ] **Step 1: Write failing tests**

Test `day_series` returns 09:15-based frame for a date with late-first-bar (first bar 09:21) and that no signal column access before first bar breaks. Test `prev_day_features` shape (one row per symbol, required columns, no NaNs where data exists).

- [ ] **Step 2: Run, expect FAIL**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_loader.py -v`

- [ ] **Step 3: Implement `loader.py`**

`day_series`: filter clean frame by calendar date. `prev_day_features`: single SQL aggregate per symbol over prior session day (D-1): gap% (open vs prev close), 20-day %move, med volume, med ₹notional per bar, 5-min-open return, last-bar close-to-high; crossbar using a calendar table of NSE trading days derived from the distinct session dates in `market_data` (dates that have a 15:29 bar). Apply `config.LIQUIDITY_GATE_CR` median notional filter; write transparent "‎D-1 feature" naming so nothing leaks current-day info.

- [ ] **Step 4: Run, expect PASS**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_loader.py -v`

- [ ] **Step 5: Smoke-verify**

Run: `./.venv/bin/python -c "from backtesting.scalpel import loader; import duckdb; c=duckdb.connect('db/historify.duckdb',read_only=True); print(loader.notional_medians(c,1723779900,1750000000)['RELIANCE'])"` then `loader.prev_day_features(c,'2026-08-14')` head. Expect sane medians (>1e7 for RELIANCE) and a clean features parquet in `cache/`.

- [ ] **Step 6: Commit**

```bash
git add backtesting/scalpel
git commit -m "feat(scalpel): loader with prev-day features and liquidity gate"
```

---

### Task 3: signal library

**Files:**
- Create: `backtesting/scalpel/signals.py`
- Create: `backtesting/scalpel/tests/test_signals.py`

**Interfaces:**
- Consumes: clean frames (no lookahead within a bar — vectors from `close[:k]`).
- Produces: `signals.build_frame(df, params) -> dict[str, np.ndarray]` with pure-numpy/only-delivered values; defaults `params = {"vwap_len": "session", "rsi_len": 2, "bb_len": 20, "bb_std": 2.0, "roll_len": 60, "or_len": 15, "rvol_len": 20, "atr_len": 14, "atr_tf_min": 5}`.

- [ ] **Step 1: Write failing tests**

Signal edge tests: (a) `vwap_z` == 0 on day-open of a synthetic flat series then grows positive on a monotonic up day; (b) `rsi2` hits 100 on two consecutive up bars, 0 on two down; (c) `roll_z` uses only past `roll_len` closes (shifted by construction — assert no element uses `close[k]` to compute z at `k` — i.e., compute with `close.shift(1)`); (d) `or_fade` is 0 before bar `or_len`, then (session_low - close)/ATR for long-fade direction; (e) all arrays length == len(df), no inf.

- [ ] **Step 2: Run, expect FAIL**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_signals.py -v`

- [ ] **Step 3: Implement `signals.py`**

Vectorized: session VWAP = cumulative (typical_price*volume) / cumsum(volume) from day start; rsi2 via Wilder on close.pct_change; bbpct = (close - mid)/ (2*std) with `rolling(bb_len).std(ddof=0)` with `.shift(1)` to avoid same-bar lookahead; roll_z same shifted-rolling; or_fade uses the first-`or_len`-minutes high/low and `atr_len` ATR (resample to 5m for `atr5`); rvol = volume / rolling-mean-volume(`rvol_len`, shifted); `trend_up = close > vwap` (boolean). Document each array's timing tap in a module docstring.

- [ ] **Step 4: Run, expect PASS**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_signals.py -v`

- [ ] **Step 5: Commit**

```bash
git add backtesting/scalpel
git commit -m "feat(scalpel): mean-reversion signal library"
```

---

### Task 4: discovery edge maps

**Files:**
- Create: `backtesting/scalpel/edge_map.py`

**Interfaces:**
- Consumes: `hygiene`, `signals`, `config`.
- Produces: `edge_map.edge_expectancy(con, symbols, start_epoch, end_epoch) -> dict[str, pd.DataFrame]` and writes `study/edge_maps/<signal>.csv` + `<signal>.png` (heatmap/expectancy vs time bucket x quantile bucket). Columns per conditioning bucket: `{n, mean_fwd_ret_bps, hit_rate, net_exp_bps, pf_approx}` for holds {1,3,5,10,15} m. Uses `cost = COST_PER_SIDE`.

- [ ] **Step 1: Implement `edge_map.py`**

For each signal in `signals.build_frame` outputs and for each conditioning axis (time bucket; trend_up; ATR quintile; signal-quantile), compute forward returns = `log(close[k+h]) - log(close[k]) - h*0` then subtract round-trip cost when evaluating expectancy for h≥3 (scalp rule: cost applies once per trade). Buckets with `n < 200` are dropped from output with a note. Runs over a **research window**: first 12 months (2024-08-16→2025-08-15) on the 60 most-liquid tradable symbols (from `notional_medians`), to keep runtime ~minutes.

- [ ] **Step 2: Run and record**

Run: `./.venv/bin/python -m backtesting.scalpelm` — use `python -c "from backtesting.scalpel import edge_map; ..."`.

Expected: `study/edge_maps/` populated; **the plan's decision point**: the top-3 signals by consistent net expectancy across buckets and holds become the candidates; the rest are dropped.

- [ ] **Step 3: Write `study/edge-summary.md`**

Human-readable markdown capturing: each candidate signal, strongest conditioning buckets, expectancy sizes in bps net, and a plain-language verdict. Commit study outputs.

- [ ] **Step 4: Commit**

```bash
git add backtesting/scalpel/study
git commit -m "feat(scalpel): discovery edge maps and summary"
```

---

### Task 5: symbol selector

**Files:**
- Create: `backtesting/scalpel/selector.py`
- Create: `backtesting/scalpel/tests/test_selector.py`

**Interfaces:**
- Consumes: `loader.prev_day_features`, `config`.
- Produces: `selector.rank_symbols(prev_features, top_n=3) -> list[str]` + `selector.rule_string()` describing exact ranking arithmetic (for the Pine screener).

- [ ] **Step 1: Write failing tests**

Tests: deterministic given input; returns only tradable symbols; no use of current-day fields; tie-break explicit; output names in NSE convention.

- [ ] **Step 2: Run, expect FAIL**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_selector.py -v`

- [ ] **Step 3: Implement `selector.py`**

Score = weighted sum of standardized prev-day features tuned toward whatever the edge maps showed, with explicit, one-line-each weights in `selector.rule_string()` (so the Pine screener can replicate score exactly from prior-day data). Ranking includes the liquidity gate and a per-day same-symbol repetition cap.

- [ ] **Step 4: Run, expect PASS**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_selector.py -v`

- [ ] **Step 5: Commit**

```bash
git add backtesting/scalpel
git commit -m "feat(scalpel): prev-day symbol selector"
```

---

### Task 6: backtest engine (entries/exits/trailing/costs)

**Files:**
- Create: `backtesting/scalpel/engine.py`
- Create: `backtesting/scalpel/tests/test_engine.py`

**Interfaces:**
- Consumes: clean frames, `signals`, `config`.
- Produces: `engine.run_strategy(bars, params, cost=0.001) -> engine.Analysis` with `.trades`, `.equity`, `.pf`, `.expectancy`, `.n_trades`, `.max_dd`.
- Default `params` (all configurable): entry on signal at `close[k]`, order at `open[k+1]`; side both (mean-reversion). `proftrigger_r=1.0` (breakeven when ≥ 1 initial stop distance in profit), `trail_r=0.5`, `stop_r=1.0` (initial stop distance in ATR5 or %), `hard_exit="15:15"`, `max_hold_bars=60`.

- [ ] **Step 1: Write failing tests**

Deterministic scenarios on synthetic bars:
- (a) **Winner→breakeven→trail:** buy, price runs +2R, stop must be at entry initially, then move to breakeven at ≥1R, then trail below rolling max; exit at trail → positive R.
- (b) **Stop hit** before 1R: exit at exactly stop distance (loss ≈ -1R – costs).
- (c) **EOD close** at `hard_exit`: no position overnight.
- (d) **Cost deduction:** a round trip with zero price change nets exactly `-2*cost`.
- (e) **No overnight / no double-entry same bar** (pyramiding=0).

- [ ] **Step 2: Run, expect FAIL**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_engine.py -v`

- [ ] **Step 3: Implement `engine.py`**

Bar-loop with event-level bookkeeping (entry price = next bar open, per-full-tick notional: pos = 1 share-size constant for analysis simplicity and % returns independent of size). Trailing state machine: `in_profit_since = max_unrealized/entry - 1 >= proftrigger_r*stop_r → stop = entry`; then `stop = max(rolling_high) - trail_r*stop_dist`; only ratchets upward (long). Apply cost = `cost * notional` on both entry and exit. Compute `r = (exit-entry)/stop_dist` (long), pf, expectancy, equity (cumprod of per-trade returns), max_dd.

- [ ] **Step 4: Run, expect PASS**

Run: `./.venv/bin/python -m pytest backtesting/scalpel/tests/test_engine.py -v`

- [ ] **Step 5: Commit**

```bash
git add backtesting/scalpel
git commit -m "feat(scalpel): vectorbt-free deterministic backtest engine with trailing exits"
```

---

### Task 7: walk-forward + robustness + sufficiency

**Files:**
- Create: `backtesting/scalpel/walkforward.py`
- Create: `backtesting/scalpel/report.py`

**Interfaces:**
- Consumes: `engine`, `selector`, `loader`, `signals`, `config`.
- Produces: `walkforward.walk_forward(con, dates, params_grid) -> dict` and a `results/walkforward-report.md`.

- [ ] **Step 1: Implement `walkforward.py`**

6 rolling folds over 2024-08-16→2026-08-14 (train 6 mo → validate 3 mo, staggered). Each fold: (a) fit/tune-only params = entry-threshold quantile + trail params by grid search on train; (b) freeze params; (c) select daily symbols via train-fitted selector; (d) run `engine` on validation; (e) record OOS trades/pf/expectancy. Also full backtest on final params. Report sufficiency: total OOS trades ≥ 500, per-symbol trade counts, per-day dispersion, win-rate by day. Robustness: rerun with each param ±10–20% and report pf/expectancy sensitivity (must not flip sign to positive from a knife-edge single point).

- [ ] **Step 2: Run and record**

Run: `./.venv/bin/python -m backtesting` driver or `python -c "from backtesting.scalpel import walkforward; ..."` writing `results/walkforward-report.md` + charts via `report.py` (plotly dark, equity curve, per-day P&L histogram, per-symbol table).

- [ ] **Step 3: Verdict gate**

**Decision point (normative):** pass if OOS pf ≥ 1.3, OOS expectancy > 0 net, trades ≥ 500, robustness sign-stable. Otherwise return to Task 4/6 with the diagnosis (do not relabel).

- [ ] **Step 4: Commit**

```bash
git add backtesting/scalpel
git commit -m "feat(scalpel): walk-forward validation and reporting"
```

---

### Task 8: Pine port + parity verification

**Files:**
- Create: `backtesting/scalpel/pine/scalpel_intraday.pine`
- Create: `backtesting/scalpel/pine/scalpel_screener.pine`
- Create: `backtesting/scalpel/verify_pine_mirror.py`

**Interfaces:**
- Consumes: final `params` + `selector.rule_string()` and the Python `engine` as reference.
- Produces: Pine `strategy()` script with identical arithmetic; a parity checker that reports per symbol trade-list/equity deltas.

- [ ] **Step 1: Implement Pine strategy**

Pine v5: `session("0915-1515", "India")`, `process_orders_on_close = true`, `pyramiding = 0`, initial capital ₹100,000, `commission_type = strategy.commission.percent`, `commission_value = 0.1` (=0.1%), slippage in points constant from config, `max_bars_back` big enough for lookback lengths. Mirror signals (`ta.vwap`, `ta.rsi`, `ta.atr`, custom rolling via `math` loops or `ta.highest`), same bar-time truncation: Pine bar time = start of the 1m bar (matches Unix bar-open seconds). Implement the same trailing state machine via `var` state vars (breakeven trigger at ≥ `proftrigger_r*stop_r`, ratchet stop = highest - trail_r*stop_dist). All params as `input.float/int`.

- [ ] **Step 2: Implement screener overlay**

`scalpel_screener.pine`: non-strategy indicator on any chart; `request.security(symseclist...)` optional; plots the `armed_score` for the chart symbol from `selector.rule_string()` using prior-day session bars (via `request.security(syminfo.tickerid, "D", close[1])` etc.) with a "ARMED/COLD" table and background highlight. Keep it deterministic and cost-independent.

- [ ] **Step 3: Parity checker**

`verify_pine_mirror.py`: for 3 symbols × 6 months, replay the exact bar series through the Python engine and through the same logic translated to numpy; compare: number of trades, entry/exit times (within ±1 bar), per-trade R (tol 1e-9), final equity (tol 1e-6). Output `results/parity-report.md`. Since TradingView is external, parity is vs a numpy port of the Pine file logic — any divergence is a bug in one of the two.

- [ ] **Step 4: Run and record**

Run: `./.venv/bin/python backtesting/scalpel/verify_pine_mirror.py`. Expect delta table with all-zero deltas.

- [ ] **Step 5: Commit**

```bash
git add backtesting/scalpel/pine backtesting/scalpel/verify_pine_mirror.py
git commit -m "feat(scalpel): Pine strategy+screener and mirror parity verification"
```

---

### Task 9: final report + verdict

**Files:**
- Create: `backtesting/scalpel/results/final-report.md`
- Create: `backtesting/scalpel/README.md`

- [ ] **Step 1: Write `final-report.md`**

Condensed: dataset+costs, candidate signals and chosen params, walk-forward results table (per fold), robustness table, per-symbol/per-day stats, Pine vs mirror parity note, **verdict (PASS/FAIL)** with exact numbers; in FAIL, include what to try next (per kill criteria).

- [ ] **Step 2: Write `README.md`**

How to reproduce: `uv run` / commands to rebuild every artifact, where files live, Pine usage instructions (load strategy, screener), parameter meanings. Ruff format both `.py` files.

- [ ] **Step 3: Final quality passes**

Run: `./.venv/bin/python -m ruff check backtesting/scalpel && ./.venv/bin/python -m ruff format backtesting/scalpel && ./.venv/bin/python -m pytest backtesting/scalpel/tests -v`. All green.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans backtesting/scalpel
git commit -m "docs(scalpel): final report and plan"
```

## Self-Review

- **Spec coverage:** §2 hygiene → Task 1; §3 discovery edge maps → Tasks 3-4; §4 selector → Task 5; §5 spec+execution → config + Task 6; §6 validation → Task 7; §7 Pine port → Task 8; §8 deliverables → file structure + Task 9; user's trailing-stop requirement → Task 6 (breakeven+trail) + Task 8 (Pine same) — covered.
- **Placeholder guard:** the only empirical steps (4, 7, 8, 9) specify commands and recorded-artefact expectations rather than hypothetical "write tests". Task 8's Pine comparison is defined as numpy port because TradingView can't be driven from CI — stated explicitly.
- **Type/constraint consistency:** single `config.py` constants referenced everywhere; one interface block lists `clean_1m_series`, `prev_day_features`, `build_frame`, `edge_expectancy`, `rank_symbols`, `run_strategy`, `walk_forward` with matching params used across tasks. Costs applied as flat 0.001/side everywhere.