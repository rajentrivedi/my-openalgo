# Walk-Forward Validation Report — Open-Window Reversion Scalp (Candlestick Family)

**Date:** 2026-08-30
**Dataset:** 89.5M 1-min bars, 500 symbols (top-100 liquid by median ₹notional used for feature build), 2024-08-16 → 2026-08-14 (494 trading days)
**Cost model:** `COST_PER_SIDE = 0.001` (20 bps round-trip), fills at open[k+1] after signal at close[k]

---

## Verdict: **FAIL** (honesty gate — gates not met)

| Gate | Target | Observed (OOS) | Verdict |
|---|---|---|---|
| Net positive expectation | > 0 | **−16.9 bps/trade net** (−693% cum over 4098 trades) | ✗ |
| Profit factor (net) | ≥ 1.3 | **0.470** | ✗ |
| OOS trade count | ≥ 500 | 4,098 | ✓ |
| Per-day consistency | majority positive | **82.6% of days net-negative** | ✗ |
| Per-symbol consistency | spread of winners | **2 of 95 symbols net-positive** | ✗ |
| Robustness (±15% params) | PF sign-stable positive | PF 0.458–0.491, uniformly negative | ✗ |

**Conclusion:** The open-window reversion candidate family fails decisively out-of-sample. This is not a tuning or selection artifact — *no* grid cell is net-profitable even in-sample (best train PF = 0.661, nowhere near 1.0, never mind 1.3). The gross edge is real but marginal, and 20 bps round-trip cost plus a 31% win rate drown it.

---

## Walk-Forward Design

6 rolling folds, staggered 3 months: fold _i_ trains on 6 months starting at `cal[0] + i*3mo`, validates on the next 3 months.

```
fold 0: train [2024-08-16 .. 2025-02-16) → val [2025-02-16 .. 2025-05-16)
fold 1: train [2024-11-16 .. 2025-05-16) → val [2025-05-16 .. 2025-08-16)
fold 2: train [2025-02-16 .. 2025-08-16) → val [2025-08-16 .. 2025-11-16)
fold 3: train [2025-05-16 .. 2025-11-16) → val [2025-11-16 .. 2026-02-16)
fold 4: train [2025-08-16 .. 2026-02-16) → val [2026-02-16 .. 2026-05-16)
fold 5: train [2025-11-16 .. 2026-05-16) → val [2026-05-16 .. 2026-08-16)
```

Per fold: **12-combo grid search on train only** (`rsi_thr ∈ {5,15,25}`, `proftrigger_r ∈ {1,2}`, `trail_r ∈ {0.5,1.0}`, `bbpct_thr=0`, `min_rvol=1.0`); selection score = `expectancy × min(n,200)` with `n≥30`; freeze params; top-3 symbols/day chosen by the train-validated selector on D-1 features; run the deterministic engine on validation days.

---

## Per-Fold Results (net of cost)

| Fold | Fitted params (rsi/proftrigger/trail) | Train n | Train PF | Val n | **Val PF** | Val exp (R) | Val net ret |
|---|---|---|---|---|---|---|---|
| 0 | 15 / 2.0 / 1.0 | 1524 | 0.654 | 643 | **0.459** | +0.076 | −121% |
| 1 | 15 / 2.0 / 0.5 | 1417 | 0.560 | 733 | **0.414** | +0.086 | −135% |
| 2 | 15 / 2.0 / 0.5 | 1376 | 0.442 | 628 | **0.636** | +0.155 | −68% |
| 3 | 15 / 2.0 / 0.5 | 1361 | 0.510 | 683 | **0.532** | +0.099 | −104% |
| 4 | 15 / 2.0 / 0.5 | 1311 | 0.580 | 681 | **0.415** | +0.030 | −135% |
| 5 | 15 / 1.0 / 1.0 | 1463 | 0.450 | 730 | **0.393** | +0.069 | −130% |
| **All** | — | — | — | 4098 | **0.470** | +0.085 | **−693%** |

Notes:
- The grid search minimizes to the same neighborhood every fold (`proftrigger_r=2.0` dominant) — the fit is stable, i.e. the negative result is *not* unstable parameter selection.
- Even gross (no-cost) OOS PF = **1.157**. Expected value of the raw signal is positive but tiny (~3 bps/trade gross) and collapses under a 20 bps round trip.

---

## Robustness (±15% perturbation on validation, frozen fitted params)

| Param | PF @ ×0.85 | PF @ ×1.15 | Exp @ ×0.85 | Exp @ ×1.15 |
|---|---|---|---|---|
| bbpct_thr | 0.475 | 0.475 | +0.086 | +0.086 |
| rsi_thr | 0.474 | 0.473 | +0.084 | +0.084 |
| proftrigger_r | 0.458 | 0.485 | +0.078 | +0.091 |
| trail_r | 0.469 | 0.474 | +0.083 | +0.084 |
| min_rvol | 0.469 | 0.491 | +0.078 | +0.089 |

All 60 perturbations (6 folds × 5 params × 2 directions) remain net-negative PF ≈ 0.46–0.49. The result does not live on a knife edge: the strategy is robustly *negative*.

---

## Sufficiency & Consistency

- **Sufficiency:** 4098 OOS trades ≥ 500 target. 367 OOS days, mean 11.2 trades/day. 95 unique symbols, mean 43 trades/symbol. Sufficiency met.
- **Per-symbol:** only 2/95 symbols net-positive (ASIANPAINT +2.0%, CANBK +0.9%); worst IDEA −55.6%R cum.
- **Per-day:** 303/367 days (82.6%) net-negative; median day −1.9%; worst 2025-03-20 (−8.4%), best 2025-09-22 (+5.7%).
- **Exit mix:** stop 2069 (−0.35% avg), max_hold 1326 (+0.05%), hard_exit 703 (−0.05%) — stops dominate losses; the trailing design captures little upside while the 1×ATR stop bleeds.

---

## Diagnosis (why it fails)

1. **Marginal gross edge, lethal cost drag.** Gross PV ≈ 1.157 is real but ~3 bps/trade average gross move after a 31% win rate; 20 bps round-trip cost removes 6× the gross edge (net ≈ −17 bps/trade).
2. **Entry threshold too eager / win rate too low.** `bbpct_thr=0` + `rsi_thr=15` fires frequently (mean 3.75 trades/symbol-day) in weak reversion conditions; longer-stop/selective variants were never net-positive either (Gate-1 fixed-hold sweep → see `edge_map.py`).
3. **1×ATR stop with breakeven+trail** converts mean-reversion winners into small fills while losers pay full stop; win rate 31% with avg win/loss far too small to overcome cost.

## What to try next (if continuing)

- **Cost reduction first:** institutional exchange-optimized pricing (~3–5 bps rt) would flip PF toward ~1.0; still not ≥1.3.
- **Selective entries:** raise `min_rvol`, tighten `rsi_thr` + add extension-colocation filter (only fade when prior-day move was extreme), reduce trades to ≤1/symbol-day.
- **Wider stop / no breakeven trigger:** test `stop_r ∈ {2,3}`, `proftrigger_r ∈ {3,4}`, and hold-to-hard-exit with strong-fade conditions only.
- **Time-of-day regime:** open-window-only entries (09:15–10:15) with daily max 1 trade, documented in report if tested.

---

## Files / Artifacts
`backtesting/scalpel/walkforward.py`, `backtesting/scalpel/feature_cache.py`, `backtesting/scalpel/loader.py`, `backtesting/scalpel/engine.py`; raw result: `/tmp/wf_result.pkl` (`walk_forward(...)` dict).

Deterministic: fold OOS PF (0.459/0.414/0.636/0.532/0.415/0.393) reproduced exactly across two independent full runs.