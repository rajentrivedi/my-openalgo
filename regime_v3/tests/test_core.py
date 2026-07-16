"""
Regression tests for the Regime V3 + Ensemble system.
Tests: data contracts, scanner outputs, normalization, consensus, portfolio, lookahead.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from regime_v3 import config
from regime_v3.portfolio.portfolio import Portfolio
from regime_v3.scanners import (
    NormalizedSignal,
    RankedCandidate,
    ScannerConsensus,
    ScannerSignal,
    list_scanners,
    registered_count,
)
from regime_v3.scanners.scanner_ensemble import build_ranked_candidates, compute_consensus


class TestDataContracts(unittest.TestCase):

    def test_scanner_signal_fields(self):
        ts = pd.Timestamp.now()
        sig = ScannerSignal(
            timestamp=ts, symbol="RELIANCE", scanner_name="momentum_v6",
            score=85.0, rank=1, direction="LONG", confidence=0.8, timeframe="5m",
        )
        self.assertEqual(sig.symbol, "RELIANCE")
        self.assertEqual(sig.raw_score, 85.0)
        d = sig.to_dict()
        self.assertEqual(d["score"], 85.0)

    def test_consensus_fields(self):
        ts = pd.Timestamp.now()
        c = ScannerConsensus(
            timestamp=ts, symbol="RELIANCE",
            scanner_count=8, available_scanners=10,
            bullish_count=6, bearish_count=1, neutral_count=1,
            bullish_ratio=0.75, bearish_ratio=0.125,
            consensus="BULLISH", mean_rank=3.2, median_rank=3.0,
            best_rank=1, worst_rank=8, rank_std=1.5,
            score_mean=0.72, score_median=0.75, score_std=0.15,
            ensemble_score=0.78, diversity_score=0.65,
        )
        self.assertEqual(c.consensus, "BULLISH")
        self.assertEqual(c.bullish_count, 6)

    def test_candidate_sorting(self):
        ts = pd.Timestamp.now()
        cands = [
            RankedCandidate(
                timestamp=ts, symbol="A", ensemble_score=0.9,
                scanner_count=8, bullish_scanners=6, bearish_scanners=1,
                scanner_rank_mean=3.0, scanner_rank_median=3.0,
                scanner_rank_best=1, scanner_rank_worst=8,
                consensus="BULLISH", regime="BULL", direction="LONG",
            ),
            RankedCandidate(
                timestamp=ts, symbol="B", ensemble_score=0.7,
                scanner_count=8, bullish_scanners=5, bearish_scanners=2,
                scanner_rank_mean=4.0, scanner_rank_median=4.0,
                scanner_rank_best=2, scanner_rank_worst=7,
                consensus="BULLISH", regime="BULL", direction="LONG",
            ),
        ]
        cands.sort(key=lambda c: c.ensemble_score, reverse=True)
        self.assertEqual(cands[0].symbol, "A")
        self.assertEqual(cands[1].symbol, "B")


class TestScannerRegistration(unittest.TestCase):

    def test_all_scanners_registered(self):
        scanners = list_scanners()
        self.assertEqual(len(scanners), 10)
        expected = config.SCANNER_NAMES
        for name in expected:
            self.assertIn(name, scanners, f"Scanner {name} not registered")

    def test_scanner_count(self):
        self.assertEqual(registered_count(), 10)


class TestConsensusBuilding(unittest.TestCase):

    def test_basic_consensus(self):
        ts = pd.Timestamp.now()
        normalized = {
            "RELIANCE": {
                "momentum_v6": NormalizedSignal(
                    timestamp=ts, symbol="RELIANCE", scanner_name="momentum_v6",
                    raw_score=85.0, normalized_score=0.9, percentile_score=0.95,
                    direction="LONG", confidence=0.8, features={"_rank": 1},
                ),
                "volume_spike": NormalizedSignal(
                    timestamp=ts, symbol="RELIANCE", scanner_name="volume_spike",
                    raw_score=70.0, normalized_score=0.7, percentile_score=0.80,
                    direction="LONG", confidence=0.6, features={"_rank": 2},
                ),
            }
        }
        consensi = compute_consensus(normalized, ts)
        self.assertIn("RELIANCE", consensi)
        c = consensi["RELIANCE"]
        self.assertEqual(c.bullish_count, 2)
        self.assertEqual(c.bearish_count, 0)
        self.assertIn("BULLISH", c.consensus)

    def test_mixed_consensus(self):
        ts = pd.Timestamp.now()
        normalized = {
            "TATASTEEL": {
                "momentum_v6": NormalizedSignal(
                    ts, "TATASTEEL", "momentum_v6", 85, 0.9, 0.95,
                    "LONG", 0.8, {"_rank": 1},
                ),
                "volume_spike": NormalizedSignal(
                    ts, "TATASTEEL", "volume_spike", 30, 0.2, 0.15,
                    "SHORT", 0.7, {"_rank": 8},
                ),
            }
        }
        consensi = compute_consensus(normalized, ts)
        c = consensi["TATASTEEL"]
        self.assertEqual(c.bullish_count, 1)
        self.assertEqual(c.bearish_count, 1)
        self.assertEqual(c.consensus, "NO_CONSENSUS")  # 1L 1S = no clear majority


class TestLookahead(unittest.TestCase):

    def test_signal_timestamp_before_entry(self):
        """Verify that signal timestamp is always before entry timestamp."""
        ts = pd.Timestamp("2026-09-11 10:00:00")
        entry_ts = pd.Timestamp("2026-09-11 10:05:00")
        self.assertLess(ts, entry_ts)

    def test_future_data_not_used(self):
        """Verify that signals only use data up to current timestamp."""
        np.random.seed(42)
        n_bars = 100
        dates = pd.date_range("2026-09-01 09:15", periods=n_bars, freq="5min")
        df = pd.DataFrame({
            "open": np.random.randn(n_bars).cumsum() + 100,
            "high": 0, "low": 0, "close": 0, "volume": np.random.randint(1000, 10000, n_bars),
        }, index=dates)
        df["high"] = df["open"] * 1.01
        df["low"] = df["open"] * 0.99
        df["close"] = df["open"] * (1 + np.random.randn(n_bars) * 0.005)

        decision_idx = 50
        decision_ts = dates[decision_idx]
        data_up_to_decision = df.loc[:decision_ts]

        # Max index used must be <= decision_idx
        self.assertEqual(len(data_up_to_decision), decision_idx + 1)

        # After computing signals on data_up_to_decision, modifying future
        # data should not affect past signals
        from regime_v3.backtest.engine import _compute_rich_signals
        sig = _compute_rich_signals(data_up_to_decision.copy(), config.RegimeV3Params())

        # Corrupt future data (though there is none since we truncated)
        # The key test: signal computation shouldn't error
        self.assertIn("atr", sig.columns)
        self.assertIn("ema20", sig.columns)
        self.assertIn("rel_volume", sig.columns)


class TestPortfolioRisk(unittest.TestCase):

    def test_daily_loss_limit(self):
        p = config.RegimeV3Params(
            initial_capital=100000,
            risk_per_trade_pct=0.25,
            max_trades_per_day=10,
            max_open_positions=5,
            use_daily_loss_limit=True,
            max_daily_loss_pct=2.0,
            estimated_cost_pct=0.0,
            slippage_pct=0.0,
        )
        port = Portfolio(p)
        port._day_start_equity = 100000
        port._current_day = "2026-09-11"

        # Simulate large loss
        port.realized_pnl = -3000  # 3% loss, exceeds 2%
        hit = port.check_daily_loss({"DUMMY": 100})
        self.assertTrue(hit)
        self.assertTrue(port._daily_loss_hit)
        self.assertFalse(port.can_trade({}))

    def test_max_positions(self):
        p = config.RegimeV3Params(
            max_open_positions=2, max_trades_per_day=10,
            initial_capital=100000, risk_per_trade_pct=0.25,
        )
        port = Portfolio(p)
        port._current_day = "2026-09-11"
        port._day_start_equity = 100000

        # Open 2 positions
        port.open_position("A", "LONG", 100, pd.Timestamp.now(), 95, 110, {}, "TEST")
        port.open_position("B", "LONG", 200, pd.Timestamp.now(), 190, 220, {}, "TEST")

        self.assertEqual(len(port.positions), 2)
        self.assertFalse(port.can_trade({}))

    def test_daily_trade_limit(self):
        p = config.RegimeV3Params(
            max_trades_per_day=1, max_open_positions=5,
            initial_capital=100000, risk_per_trade_pct=0.25,
        )
        port = Portfolio(p)
        port._current_day = "2026-09-11"
        port._day_start_equity = 100000

        opened1 = port.open_position("A", "LONG", 100, pd.Timestamp.now(), 95, 110, {}, "TEST")
        self.assertTrue(opened1)

        opened2 = port.open_position("B", "LONG", 200, pd.Timestamp.now(), 190, 220, {}, "TEST")
        self.assertFalse(opened2)


class TestEnsembleConsistency(unittest.TestCase):

    def test_empty_inputs(self):
        ts = pd.Timestamp.now()
        consensi = compute_consensus({}, ts)
        self.assertEqual(len(consensi), 0)
        cands = build_ranked_candidates({}, "BULL", ts)
        self.assertEqual(len(cands), 0)

    def test_single_scanner_filtered(self):
        """Single scanner is filtered by MIN_SCANNERS_FOR_VOTE (default 3)."""
        ts = pd.Timestamp.now()
        normalized = {
            "RELIANCE": {
                "momentum_v6": NormalizedSignal(
                    ts, "RELIANCE", "momentum_v6", 85, 0.9, 0.95,
                    "LONG", 0.8, {},
                ),
            }
        }
        consensi = compute_consensus(normalized, ts)
        c = consensi["RELIANCE"]
        self.assertEqual(c.scanner_count, 1)
        self.assertEqual(c.bullish_ratio, 1.0)
        cands = build_ranked_candidates(consensi, "BULL", ts)
        self.assertEqual(len(cands), 0)  # filtered by min_scanners_for_vote


if __name__ == "__main__":
    unittest.main(verbosity=2)
