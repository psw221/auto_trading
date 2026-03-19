from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from auto_trading.broker.dto import BrokerRealtimeEvent
from auto_trading.common.time import utc_now
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
        snapshot = MarketSnapshot(symbol=event.symbol, price=price, volume=volume, turnover=turnover, source='WS', refreshed_at=utc_now().isoformat())
        self.cache.set(snapshot)
        self.cache.mark_refresh_success(event.symbol, source='WS')

    def replace_bars(self, symbol: str, bars: list[Bar]) -> None:
        if not symbol:
            return
        container = self.cache.bars[symbol]
        container.clear()
        for bar in bars:
            container.append(bar)

    def set_rest_market_data(
        self,
        symbol: str,
        snapshot: MarketSnapshot,
        bars: list[Bar],
        *,
        refreshed_at: datetime | None = None,
    ) -> None:
        refreshed = refreshed_at or utc_now()
        snapshot.source = 'REST'
        snapshot.refreshed_at = refreshed.isoformat()
        self.cache.set(snapshot)
        self.replace_bars(symbol, bars)
        self.cache.mark_refresh_success(symbol, source='REST', occurred_at=refreshed)

    def record_refresh_failure(self, symbol: str, error: str, *, occurred_at: datetime | None = None) -> None:
        if not symbol:
            return
        self.cache.mark_refresh_failure(symbol, error, occurred_at=occurred_at)

    def build_refresh_summary(
        self,
        symbols: list[str],
        *,
        stale_after_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, object]:
        seen: list[str] = []
        seen_set: set[str] = set()
        for symbol in symbols:
            if not symbol or symbol in seen_set:
                continue
            seen_set.add(symbol)
            seen.append(symbol)
        current = now or utc_now()
        refreshed_count = 0
        failed_count = 0
        stale_symbols: list[str] = []
        failed_symbols: list[str] = []
        latest_refresh_at = ''
        for status in self.cache.get_refresh_statuses(seen):
            if status.last_success_at:
                refreshed_count += 1
                latest_refresh_at = max(latest_refresh_at, status.last_success_at)
                try:
                    refreshed_at = datetime.fromisoformat(status.last_success_at)
                except ValueError:
                    refreshed_at = None
                if refreshed_at is None or (current - refreshed_at).total_seconds() > stale_after_seconds:
                    stale_symbols.append(status.symbol)
            if status.last_failure_at:
                failed_count += 1
                failed_symbols.append(status.symbol)
        missing_symbols = [symbol for symbol in seen if self.cache.get_refresh_status(symbol) is None]
        stale_symbols.extend(symbol for symbol in missing_symbols if symbol not in stale_symbols)
        return {
            'snapshot_time': current.isoformat(),
            'requested_count': len(seen),
            'refreshed_count': refreshed_count,
            'failed_count': failed_count,
            'stale_symbol_count': len(stale_symbols),
            'latest_refresh_at': latest_refresh_at,
            'failed_symbols': failed_symbols[:10],
            'stale_symbols': stale_symbols[:10],
        }

    def get_latest_snapshot(self, symbol: str) -> MarketSnapshot | None:
        return self.cache.get(symbol)

    def get_recent_bars(self, symbol: str, window: int) -> list[Bar]:
        return self.cache.get_bars(symbol, window)
