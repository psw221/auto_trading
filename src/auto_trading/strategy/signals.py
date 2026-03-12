from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from auto_trading.portfolio.models import Position
from auto_trading.strategy.models import EntrySignal, ExitSignal, MarketSnapshot, StrategyScore


@dataclass(slots=True)
class SignalEngine:
    def evaluate_entry(self, candidates: list[StrategyScore]) -> list[EntrySignal]:
        qualified = [item for item in candidates if item.score_total >= 70]
        qualified.sort(key=lambda item: item.score_total, reverse=True)
        return [EntrySignal(symbol=item.symbol, score_total=item.score_total, price=item.price) for item in qualified]

    def evaluate_exit(self, position: Position, snapshot: MarketSnapshot) -> ExitSignal | None:
        if position.avg_entry_price > 0 and snapshot.price <= position.avg_entry_price * 0.985:
            return ExitSignal(symbol=position.symbol, reason="stop_loss", order_type="MARKET")
        if position.avg_entry_price > 0 and snapshot.price >= position.avg_entry_price * 1.04:
            return ExitSignal(symbol=position.symbol, reason="take_profit", order_type="LIMIT")
        if snapshot.ma5 > 0 and snapshot.price < snapshot.ma5:
            return ExitSignal(symbol=position.symbol, reason="ma5_breakdown", order_type="MARKET")
        if self._holding_days(position) > 5:
            return ExitSignal(symbol=position.symbol, reason="time_exit", order_type="MARKET")
        return None

    @staticmethod
    def _holding_days(position: Position) -> int:
        if not position.opened_at:
            return 0
        opened_at = datetime.fromisoformat(position.opened_at)
        now = datetime.now(opened_at.tzinfo or timezone.utc)
        return (now.date() - opened_at.date()).days
