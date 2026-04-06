from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path

from auto_trading.config.schema import Settings
from auto_trading.risk.engine import RiskEngine
from auto_trading.strategy.models import EntrySignal


@dataclass(slots=True)
class _Portfolio:
    total_asset: float
    open_positions: list[object] = field(default_factory=list)


def build_settings() -> Settings:
    return Settings(
        env="demo",
        db_path=Path("data/test_risk_engine.db"),
        kis_base_url="https://example.com",
        kis_ws_url="ws://example.com",
        kis_app_key="key",
        kis_app_secret="secret",
        kis_cano="123",
        kis_acnt_prdt_cd="01",
        kis_access_token="token",
        kis_refresh_token="",
        kis_user_id="user1",
        universe_master_path=Path("data/universe_master.csv"),
        holiday_calendar_path=Path("data/krx_holidays.csv"),
        holiday_api_service_key="",
        rest_min_interval_seconds=0.12,
        telegram_bot_token="",
        telegram_chat_id="",
    )


class RiskEngineSizingTest(unittest.TestCase):
    def test_can_enter_rejects_zero_total_asset(self) -> None:
        engine = RiskEngine(build_settings())
        decision = engine.can_enter(EntrySignal(symbol="005930", score_total=80, price=70000.0), _Portfolio(total_asset=0.0))
        self.assertFalse(decision.allowed)
        self.assertEqual("invalid_portfolio_value", decision.reason)

    def test_can_enter_rejects_when_base_amount_cannot_buy_one_share(self) -> None:
        engine = RiskEngine(build_settings())
        portfolio = _Portfolio(total_asset=200000.0)  # 25% -> 50,000
        decision = engine.can_enter(EntrySignal(symbol="005930", score_total=80, price=70000.0), portfolio)
        self.assertFalse(decision.allowed)
        self.assertEqual("insufficient_order_budget", decision.reason)

    def test_target_order_size_returns_multiple_shares_when_budget_is_sufficient(self) -> None:
        engine = RiskEngine(build_settings())
        portfolio = _Portfolio(total_asset=1000000.0)  # 25% -> 250,000
        sizing = engine.target_order_size(EntrySignal(symbol="005930", score_total=80, price=70000.0), portfolio)
        self.assertEqual(3, sizing.qty)


if __name__ == "__main__":
    unittest.main()
