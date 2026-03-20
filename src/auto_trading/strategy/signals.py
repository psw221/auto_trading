from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from auto_trading.portfolio.models import Position
from auto_trading.strategy.models import EntrySignal, ExitSignal, MarketSnapshot, StrategyScore


@dataclass(slots=True)
class SignalEngine:
    def evaluate_entry(self, candidates: list[StrategyScore]) -> list[EntrySignal]:
        qualified = [item for item in candidates if item.score_total >= 70]
        qualified.sort(key=lambda item: item.score_total, reverse=True)
        return [EntrySignal(symbol=item.symbol, score_total=item.score_total, price=item.price) for item in qualified]

    def evaluate_exit(self, position: Position, snapshot: MarketSnapshot) -> ExitSignal | None:
        has_fresh_price = snapshot.price > 0 and not snapshot.is_stale
        if has_fresh_price and position.avg_entry_price > 0 and snapshot.price <= position.avg_entry_price * 0.985:
            return ExitSignal(symbol=position.symbol, reason="stop_loss", order_type="MARKET")
        if has_fresh_price and position.avg_entry_price > 0 and snapshot.price >= position.avg_entry_price * 1.04:
            return ExitSignal(
                symbol=position.symbol,
                reason="take_profit",
                order_type="LIMIT",
                price=snapshot.price,
            )
        if has_fresh_price and snapshot.indicators_ready and snapshot.ma5 > 0 and snapshot.price < snapshot.ma5:
            return ExitSignal(symbol=position.symbol, reason="ma5_breakdown", order_type="MARKET")
        if self._holding_days(position) > 5:
            return ExitSignal(symbol=position.symbol, reason="time_exit", order_type="MARKET")
        return None

    @staticmethod
    def _holding_days(position: Position) -> int:
        opened_at = SignalEngine._parse_position_opened_at(position)
        if opened_at is None:
            return 0
        now = datetime.now(opened_at.tzinfo or timezone.utc)
        return (now.date() - opened_at.date()).days

    @staticmethod
    def _parse_position_opened_at(position: Position) -> datetime | None:
        raw = str(position.opened_at or '').strip()
        if not raw:
            return None
        try:
            opened_at = datetime.fromisoformat(raw)
        except ValueError:
            if len(raw) != 6 or not raw.isdigit():
                return None
            fallback = SignalEngine._parse_fallback_timestamp(
                getattr(position, 'created_at', None),
                getattr(position, 'updated_at', None),
            )
            if fallback is None:
                return None
            seoul = timezone(timedelta(hours=9))
            fallback_local = fallback.astimezone(seoul)
            try:
                return datetime(
                    fallback_local.year,
                    fallback_local.month,
                    fallback_local.day,
                    int(raw[0:2]),
                    int(raw[2:4]),
                    int(raw[4:6]),
                    tzinfo=seoul,
                )
            except ValueError:
                return None
        if opened_at.tzinfo is None:
            return opened_at.replace(tzinfo=timezone.utc)
        return opened_at

    @staticmethod
    def _parse_fallback_timestamp(*values: object) -> datetime | None:
        for value in values:
            raw = str(value or '').strip()
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return None
