from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from auto_trading.strategy.models import Bar, MarketSnapshot


@dataclass(slots=True)
class MarketDataCache:
    snapshots: dict[str, MarketSnapshot] = field(default_factory=dict)
    bars: dict[str, deque[Bar]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=240)))

    def set(self, snapshot: MarketSnapshot) -> None:
        self.snapshots[snapshot.symbol] = snapshot

    def get(self, symbol: str) -> MarketSnapshot | None:
        return self.snapshots.get(symbol)

    def append_bar(self, bar: Bar) -> None:
        self.bars[bar.symbol].append(bar)

    def get_bars(self, symbol: str, window: int) -> list[Bar]:
        return list(self.bars.get(symbol, deque()))[-window:]
