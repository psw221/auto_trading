from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from auto_trading.portfolio.models import Position
from auto_trading.strategy.models import MarketSnapshot
from auto_trading.strategy.signals import SignalEngine


class SignalEngineExitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SignalEngine()

    def test_stop_loss_has_priority(self) -> None:
        position = Position(symbol="005930", qty=1, avg_entry_price=100.0, opened_at=datetime.now(timezone.utc).isoformat())
        snapshot = MarketSnapshot(symbol="005930", price=98.0, ma5=101.0)
        signal = self.engine.evaluate_exit(position, snapshot)
        self.assertIsNotNone(signal)
        self.assertEqual("stop_loss", signal.reason)
        self.assertEqual("MARKET", signal.order_type)

    def test_take_profit_uses_limit_order(self) -> None:
        position = Position(symbol="005930", qty=1, avg_entry_price=100.0, opened_at=datetime.now(timezone.utc).isoformat())
        snapshot = MarketSnapshot(symbol="005930", price=104.5, ma5=103.0)
        signal = self.engine.evaluate_exit(position, snapshot)
        self.assertIsNotNone(signal)
        self.assertEqual("take_profit", signal.reason)
        self.assertEqual("LIMIT", signal.order_type)
        self.assertEqual(104.5, signal.price)

    def test_ma5_breakdown_triggers_market_exit(self) -> None:
        position = Position(symbol="005930", qty=1, avg_entry_price=100.0, opened_at=datetime.now(timezone.utc).isoformat())
        snapshot = MarketSnapshot(symbol="005930", price=101.0, ma5=102.0, indicators_ready=True)
        signal = self.engine.evaluate_exit(position, snapshot)
        self.assertIsNotNone(signal)
        self.assertEqual("ma5_breakdown", signal.reason)

    def test_stale_snapshot_blocks_price_based_exit(self) -> None:
        position = Position(symbol="005930", qty=1, avg_entry_price=100.0, opened_at=datetime.now(timezone.utc).isoformat())
        snapshot = MarketSnapshot(symbol="005930", price=98.0, ma5=101.0, is_stale=True)
        signal = self.engine.evaluate_exit(position, snapshot)
        self.assertIsNone(signal)

    def test_ma5_breakdown_requires_indicator_ready(self) -> None:
        position = Position(symbol="005930", qty=1, avg_entry_price=100.0, opened_at=datetime.now(timezone.utc).isoformat())
        snapshot = MarketSnapshot(symbol="005930", price=101.0, ma5=102.0, indicators_ready=False)
        signal = self.engine.evaluate_exit(position, snapshot)
        self.assertIsNone(signal)

    def test_time_exit_is_allowed_even_when_snapshot_is_stale(self) -> None:
        opened_at = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
        position = Position(symbol="005930", qty=1, avg_entry_price=100.0, opened_at=opened_at)
        snapshot = MarketSnapshot(symbol="005930", price=0.0, is_stale=True)
        signal = self.engine.evaluate_exit(position, snapshot)
        self.assertIsNotNone(signal)
        self.assertEqual("time_exit", signal.reason)

    def test_time_exit_after_five_days(self) -> None:
        opened_at = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
        position = Position(symbol="005930", qty=1, avg_entry_price=100.0, opened_at=opened_at)
        snapshot = MarketSnapshot(symbol="005930", price=101.5, ma5=100.0)
        signal = self.engine.evaluate_exit(position, snapshot)
        self.assertIsNotNone(signal)
        self.assertEqual("time_exit", signal.reason)
        self.assertEqual("MARKET", signal.order_type)


if __name__ == "__main__":
    unittest.main()
