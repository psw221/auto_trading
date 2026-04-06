from __future__ import annotations

from dataclasses import dataclass

from auto_trading.config.schema import Settings
from auto_trading.strategy.models import EntrySignal, ExitSignal, OrderSizing, RiskDecision


@dataclass(slots=True)
class RiskEngine:
    settings: Settings

    def can_enter(self, signal: EntrySignal, portfolio: object) -> RiskDecision:
        open_positions = list(getattr(portfolio, 'open_positions', []) or [])
        if any(getattr(position, 'symbol', '') == signal.symbol for position in open_positions):
            return RiskDecision(False, "already_holding")
        if len(open_positions) >= self.settings.max_positions:
            return RiskDecision(False, "max_positions")
        total_asset = float(getattr(portfolio, 'total_asset', 0.0) or 0.0)
        if total_asset <= 0.0:
            return RiskDecision(False, "invalid_portfolio_value")
        base_amount = total_asset * self.settings.base_weight
        if base_amount < max(signal.price, 1.0):
            return RiskDecision(False, "insufficient_order_budget")
        return RiskDecision(True, "ok")

    def can_exit(self, signal: ExitSignal, portfolio: object) -> RiskDecision:
        return RiskDecision(True, "ok")

    def target_order_size(self, signal: EntrySignal, portfolio: object) -> OrderSizing:
        base_amount = float(getattr(portfolio, 'total_asset', 0.0) or 0.0) * self.settings.base_weight
        qty = int(base_amount // max(signal.price, 1))
        return OrderSizing(qty=max(qty, 0), order_type="LIMIT", price=signal.price)
