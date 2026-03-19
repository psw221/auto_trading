from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

from auto_trading.common.time import utc_now
from auto_trading.strategy.models import Bar, MarketSnapshot


@dataclass(slots=True)
class MarketDataRefreshStatus:
    symbol: str
    source: str = ''
    last_attempt_at: str = ''
    last_success_at: str = ''
    last_failure_at: str = ''
    failure_count: int = 0
    last_error: str = ''


@dataclass(slots=True)
class MarketDataCache:
    snapshots: dict[str, MarketSnapshot] = field(default_factory=dict)
    bars: dict[str, deque[Bar]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=240)))
    refresh_statuses: dict[str, MarketDataRefreshStatus] = field(default_factory=dict)

    def set(self, snapshot: MarketSnapshot) -> None:
        self.snapshots[snapshot.symbol] = snapshot

    def get(self, symbol: str) -> MarketSnapshot | None:
        return self.snapshots.get(symbol)

    def append_bar(self, bar: Bar) -> None:
        self.bars[bar.symbol].append(bar)

    def get_bars(self, symbol: str, window: int) -> list[Bar]:
        return list(self.bars.get(symbol, deque()))[-window:]

    def mark_refresh_success(
        self,
        symbol: str,
        *,
        source: str,
        occurred_at: datetime | None = None,
    ) -> None:
        status = self.refresh_statuses.get(symbol) or MarketDataRefreshStatus(symbol=symbol)
        timestamp = (occurred_at or utc_now()).isoformat()
        status.source = source
        status.last_attempt_at = timestamp
        status.last_success_at = timestamp
        status.last_error = ''
        self.refresh_statuses[symbol] = status

    def mark_refresh_failure(self, symbol: str, error: str, *, occurred_at: datetime | None = None) -> None:
        status = self.refresh_statuses.get(symbol) or MarketDataRefreshStatus(symbol=symbol)
        timestamp = (occurred_at or utc_now()).isoformat()
        status.last_attempt_at = timestamp
        status.last_failure_at = timestamp
        status.failure_count += 1
        status.last_error = error
        self.refresh_statuses[symbol] = status

    def get_refresh_status(self, symbol: str) -> MarketDataRefreshStatus | None:
        return self.refresh_statuses.get(symbol)

    def get_refresh_statuses(self, symbols: list[str]) -> list[MarketDataRefreshStatus]:
        seen: set[str] = set()
        selected: list[MarketDataRefreshStatus] = []
        for symbol in symbols:
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            status = self.refresh_statuses.get(symbol)
            if status is not None:
                selected.append(status)
        return selected
