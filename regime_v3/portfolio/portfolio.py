"""
Shared portfolio engine — single equity account, risk-based sizing, daily limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..scanners.scanner_signal import RankedCandidate


@dataclass
class Position:
    symbol: str
    direction: str
    quantity: int
    entry_price: float
    entry_time: Any
    stop_price: float
    target_price: float
    risk_amount: float
    mfe: float = 0.0
    mae: float = 0.0
    holding_bars: int = 0
    strategy: str = ""
    regime: str = ""
    selection_method: str = ""
    llm_score: float = 0.0


@dataclass
class Trade:
    symbol: str
    direction: str
    strategy: str
    regime: str
    selection_method: str
    llm_score: float
    quantity: int
    entry_price: float
    entry_time: Any
    exit_price: float
    exit_time: Any
    exit_reason: str
    gross_pnl: float
    cost: float
    slippage_cost: float
    net_pnl: float
    risk_amount: float
    r_multiple: float
    mfe: float
    mae: float
    holding_bars: int
    holding_minutes: float

    def to_dict(self) -> dict:
        return {k: (str(v) if hasattr(v, "strftime") else v) for k, v in self.__dict__.items()}


class Portfolio:
    """
    Single shared equity account.
    Handles position sizing, daily loss checks, and trade simulation.
    """

    def __init__(self, p: config.RegimeV3Params):
        self.p = p
        self.equity = p.initial_capital
        self.realized_pnl = 0.0
        self.peak_equity = p.initial_capital
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.trade_counter = 0

        self._current_day: str | None = None
        self._day_start_equity = p.initial_capital
        self._daily_trade_count = 0
        self._daily_loss_hit = False

    def reset_day(self, day: str, current_prices: dict[str, float]):
        if day != self._current_day:
            self._current_day = day
            unreal = self._mark_to_market(current_prices)
            self._day_start_equity = self.p.initial_capital + self.realized_pnl + unreal
            self._daily_trade_count = 0
            self._daily_loss_hit = False

    def _mark_to_market(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym, pos.entry_price)
            if pos.direction == "LONG":
                total += (px - pos.entry_price) * pos.quantity
            else:
                total += (pos.entry_price - px) * pos.quantity
        return total

    def total_equity(self, current_prices: dict[str, float]) -> float:
        return self.p.initial_capital + self.realized_pnl + self._mark_to_market(current_prices)

    def can_trade(self, current_prices: dict[str, float]) -> bool:
        if self.p.force_no_trade:
            return False
        if self._daily_trade_count >= self.p.max_trades_per_day:
            return False
        if self._daily_loss_hit:
            return False
        if len(self.positions) >= self.p.max_open_positions:
            return False
        return True

    def check_daily_loss(self, current_prices: dict[str, float]) -> bool:
        if not self.p.use_daily_loss_limit or self._daily_loss_hit:
            return False
        total_eq = self.total_equity(current_prices)
        daily_pnl = total_eq - self._day_start_equity
        if daily_pnl <= -(self._day_start_equity * self.p.max_daily_loss_pct / 100.0):
            self._daily_loss_hit = True
            return True
        return False

    def close_all_positions(self, prices: dict[str, float], reason: str):
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            exit_px = prices.get(sym, pos.entry_price)
            self._close_position(sym, exit_px, reason)

    def _close_position(self, symbol: str, exit_price: float, reason: str):
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return

        if pos.direction == "LONG":
            gross = (exit_price - pos.entry_price) * pos.quantity
        else:
            gross = (pos.entry_price - exit_price) * pos.quantity

        turnover = abs(pos.entry_price * pos.quantity) + abs(exit_price * pos.quantity)
        cost = turnover * self.p.estimated_cost_pct / 100.0
        slippage = turnover * self.p.slippage_pct / 100.0
        net = gross - cost - slippage

        self.realized_pnl += net
        self.peak_equity = max(self.peak_equity, self.p.initial_capital + self.realized_pnl)

        trade = Trade(
            symbol=pos.symbol,
            direction=pos.direction,
            strategy=pos.strategy,
            regime=pos.regime,
            selection_method=pos.selection_method,
            llm_score=pos.llm_score,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=pd.Timestamp.now(),
            exit_reason=reason,
            gross_pnl=gross,
            cost=cost,
            slippage_cost=slippage,
            net_pnl=net,
            risk_amount=pos.risk_amount,
            r_multiple=round(gross / max(pos.risk_amount, 1e-10), 4),
            mfe=pos.mfe,
            mae=pos.mae,
            holding_bars=pos.holding_bars,
            holding_minutes=0.0,
        )
        self.trades.append(trade)

    def open_position(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        entry_time: Any,
        stop_price: float,
        target_price: float,
        current_prices: dict[str, float],
        selection_method: str,
        llm_score: float = 0.0,
        strategy: str = "",
        regime: str = "",
    ) -> bool:
        if not self.can_trade(current_prices):
            return False
        if symbol in self.positions:
            return False

        eq_now = self.total_equity(current_prices)
        risk = abs(entry_price - stop_price)
        if risk <= 0:
            return False

        risk_amt = eq_now * self.p.risk_per_trade_pct / 100.0
        qty = max(1, int(risk_amt / risk))
        max_val = eq_now * self.p.position_value_pct / 100.0
        qty = min(qty, int(max_val / entry_price) if entry_price > 0 else qty)

        self.positions[symbol] = Position(
            symbol=symbol,
            direction=direction,
            quantity=qty,
            entry_price=entry_price,
            entry_time=entry_time,
            stop_price=stop_price,
            target_price=target_price,
            risk_amount=risk * qty,
            strategy=strategy,
            regime=regime,
            selection_method=selection_method,
            llm_score=llm_score,
        )
        self._daily_trade_count += 1
        return True

    def manage_positions(
        self,
        prices: dict[str, dict],  # symbol -> {open, high, low, close}
        timestamp: Any,
        force_square_off: bool = False,
    ):
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            data = prices.get(sym, {})
            close = data.get("close", 0.0)
            high = data.get("high", close)
            low = data.get("low", close)

            pos.holding_bars += 1
            if pos.direction == "LONG":
                pos.mfe = max(pos.mfe, high - pos.entry_price)
                pos.mae = min(pos.mae, low - pos.entry_price)

                if low <= pos.stop_price:
                    self._close_position(sym, pos.stop_price, "STOP_LOSS")
                elif high >= pos.target_price:
                    self._close_position(sym, pos.target_price, "TARGET")
            else:
                pos.mfe = max(pos.mfe, pos.entry_price - low)
                pos.mae = max(pos.mae, pos.entry_price - high)

                if high >= pos.stop_price:
                    self._close_position(sym, pos.stop_price, "STOP_LOSS")
                elif low <= pos.target_price:
                    self._close_position(sym, pos.target_price, "TARGET")

            if force_square_off and sym in self.positions:
                self._close_position(sym, close, "EOD_SQUARE_OFF")

    def get_metrics(self) -> dict:
        if not self.trades:
            return {
                "n": 0, "gross": 0.0, "cost": 0.0, "net": 0.0,
                "wr": 0.0, "pf": 0.0, "avg_r": 0.0, "max_dd": 0.0,
            }
        df = pd.DataFrame([t.to_dict() for t in self.trades])
        net_pnl = df["net_pnl"]
        pos = net_pnl[net_pnl > 0].sum() if (net_pnl > 0).any() else 0
        neg = net_pnl[net_pnl < 0].sum() if (net_pnl < 0).any() else 0
        pf = abs(pos / neg) if neg != 0 else (float("inf") if pos > 0 else 0.0)
        max_dd = 0.0
        eq = self.p.initial_capital + df["net_pnl"].cumsum()
        pk = eq.cummax()
        dd = (pk - eq) / pk * 100
        max_dd = float(dd.max()) if len(dd) else 0.0

        return {
            "n": len(df),
            "gross": float(df["gross_pnl"].sum()),
            "cost": float(df["cost"].sum()),
            "net": float(net_pnl.sum()),
            "wr": float((net_pnl > 0).mean() * 100) if len(net_pnl) else 0.0,
            "pf": round(float(pf), 4),
            "avg_r": float(df["r_multiple"].mean()) if "r_multiple" in df.columns else 0.0,
            "max_dd": round(max_dd, 4),
        }
