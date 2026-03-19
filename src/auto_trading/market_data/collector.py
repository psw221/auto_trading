from __future__ import annotations

from dataclasses import dataclass

from auto_trading.broker.dto import BrokerRealtimeEvent
from auto_trading.market_data.cache import MarketDataCache
from auto_trading.strategy.models import Bar, MarketSnapshot


@dataclass(slots=True)
class MarketDataCollector:
    cache: MarketDataCache

    def update_quote(self, event: BrokerRealtimeEvent) -> None:
        if not event.symbol:
            return
        price = float(event.payload.get("price", 0.0))
        volume = float(event.payload.get("volume", 0.0))
        turnover = float(event.payload.get("turnover", 0.0))
        bar = Bar(
            symbol=event.symbol,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            turnover=turnover,
        )
        self.cache.append_bar(bar)
        snapshot = MarketSnapshot(symbol=event.symbol, price=price, volume=volume, turnover=turnover)
        self.cache.set(snapshot)

    def replace_bars(self, symbol: str, bars: list[Bar]) -> None:
        if not symbol:
            return
        container = self.cache.bars[symbol]
        container.clear()
        for bar in bars:
            container.append(bar)

    def set_rest_market_data(self, symbol: str, snapshot: MarketSnapshot, bars: list[Bar]) -> None:
        self.cache.set(snapshot)
        self.replace_bars(symbol, bars)

    def get_latest_snapshot(self, symbol: str) -> MarketSnapshot | None:
        return self.cache.get(symbol)

    def get_recent_bars(self, symbol: str, window: int) -> list[Bar]:
        return self.cache.get_bars(symbol, window)
