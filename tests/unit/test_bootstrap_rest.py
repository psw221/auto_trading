from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from auto_trading.app.bootstrap import _build_validated_rest_market_data, _refresh_market_data_from_rest
from auto_trading.market_data.cache import MarketDataCache
from auto_trading.market_data.collector import MarketDataCollector


@dataclass(slots=True)
class _StubKISClient:
    current_calls: list[str] = field(default_factory=list)
    bars_calls: list[str] = field(default_factory=list)

    def get_current_price(self, symbol: str) -> dict[str, object]:
        self.current_calls.append(symbol)
        return {'price': 1000.0, 'turnover': 1000000.0}

    def get_daily_bars(self, symbol: str, lookback_days: int = 30) -> list[dict[str, object]]:
        self.bars_calls.append(symbol)
        return [
            {
                'open': 900.0,
                'high': 1100.0,
                'low': 850.0,
                'close': 950.0,
                'volume': 1000.0,
                'turnover': 1000000.0,
            }
            for _ in range(30)
        ]


class BootstrapRestRefreshTest(unittest.TestCase):
    def test_refresh_prioritizes_holdings_before_scan_symbols(self) -> None:
        client = _StubKISClient()
        collector = MarketDataCollector(MarketDataCache())
        result = _refresh_market_data_from_rest(
            {
                'priority_symbols': ['088350'],
                'scan_symbols': ['000001', '000002'],
                'universe_refresh_interval_seconds': 90,
            },
            client,
            collector,
        )
        self.assertEqual(['088350', '000001', '000002'], client.current_calls)
        self.assertEqual(['088350', '000001', '000002'], client.bars_calls)
        self.assertEqual(3, result['requested_count'])
        self.assertEqual(3, result['attempted_count'])
        self.assertEqual(3, result['refreshed_count'])
        self.assertEqual(0, result['skipped_count'])
        self.assertEqual(1, result['priority_count'])

    def test_refresh_reuses_recent_universe_data_but_not_priority_holdings(self) -> None:
        client = _StubKISClient()
        collector = MarketDataCollector(MarketDataCache())
        collector.set_rest_market_data(
            '000001',
            snapshot=type('Snapshot', (), {'symbol': '000001', 'price': 1000.0, 'volume': 0.0, 'turnover': 1000000.0})(),
            bars=[],
        )
        collector.set_rest_market_data(
            '088350',
            snapshot=type('Snapshot', (), {'symbol': '088350', 'price': 1000.0, 'volume': 0.0, 'turnover': 1000000.0})(),
            bars=[],
        )
        result = _refresh_market_data_from_rest(
            {
                'priority_symbols': ['088350'],
                'scan_symbols': ['000001'],
                'universe_refresh_interval_seconds': 90,
            },
            client,
            collector,
        )
        self.assertEqual(['088350'], client.current_calls)
        self.assertEqual(['088350'], client.bars_calls)
        self.assertEqual(2, result['requested_count'])
        self.assertEqual(1, result['attempted_count'])
        self.assertEqual(1, result['refreshed_count'])
        self.assertEqual(1, result['skipped_count'])
        self.assertEqual(['000001'], result['skipped_symbols'])


if __name__ == '__main__':
    unittest.main()


class _EmptyBarsKISClient(_StubKISClient):
    def get_daily_bars(self, symbol: str, lookback_days: int = 30) -> list[dict[str, object]]:
        self.bars_calls.append(symbol)
        return []


class _ZeroPriceKISClient(_StubKISClient):
    def get_current_price(self, symbol: str) -> dict[str, object]:
        self.current_calls.append(symbol)
        return {'price': 0.0, 'turnover': 0.0}


class BootstrapRestValidationTest(unittest.TestCase):
    def test_build_validated_rest_market_data_requires_positive_current_price(self) -> None:
        with self.assertRaisesRegex(ValueError, 'current price missing or zero'):
            _build_validated_rest_market_data('005930', current={'price': 0.0, 'turnover': 0.0}, history=[{'close': 70000.0}] * 30)

    def test_build_validated_rest_market_data_requires_sufficient_daily_bars(self) -> None:
        with self.assertRaisesRegex(ValueError, 'daily bars missing or insufficient'):
            _build_validated_rest_market_data('005930', current={'price': 70000.0, 'turnover': 1000.0}, history=[])

    def test_refresh_records_failure_and_preserves_existing_cache_when_daily_bars_are_empty(self) -> None:
        client = _EmptyBarsKISClient()
        collector = MarketDataCollector(MarketDataCache())
        collector.set_rest_market_data(
            '005930',
            snapshot=type('Snapshot', (), {'symbol': '005930', 'price': 71000.0, 'volume': 0.0, 'turnover': 1000000.0})(),
            bars=[type('Bar', (), {'symbol': '005930', 'open': 70000.0, 'high': 71000.0, 'low': 69000.0, 'close': 70500.0, 'volume': 1000.0, 'turnover': 1000000.0})() for _ in range(30)],
        )
        result = _refresh_market_data_from_rest(
            {
                'priority_symbols': ['005930'],
                'scan_symbols': [],
                'universe_refresh_interval_seconds': 90,
            },
            client,
            collector,
        )
        self.assertEqual(1, result['attempted_count'])
        self.assertEqual(0, result['refreshed_count'])
        self.assertEqual(1, result['failed_count'])
        self.assertEqual(['005930'], result['failed_symbols'])
        self.assertEqual([{'symbol': '005930', 'reason': 'INSUFFICIENT_BARS', 'error': 'Broker daily bars missing or insufficient for 005930. bars=0'}], result['failed_details'])
        self.assertEqual(30, len(collector.get_recent_bars('005930', 30)))
        self.assertEqual(71000.0, collector.get_latest_snapshot('005930').price)
        status = collector.cache.get_refresh_status('005930')
        self.assertIsNotNone(status)
        self.assertTrue(bool(status.last_failure_at))

    def test_refresh_records_failure_when_current_price_is_zero(self) -> None:
        client = _ZeroPriceKISClient()
        collector = MarketDataCollector(MarketDataCache())
        result = _refresh_market_data_from_rest(
            {
                'priority_symbols': ['005930'],
                'scan_symbols': [],
                'universe_refresh_interval_seconds': 90,
            },
            client,
            collector,
        )
        self.assertEqual(1, result['attempted_count'])
        self.assertEqual(0, result['refreshed_count'])
        self.assertEqual(1, result['failed_count'])
        self.assertEqual(['005930'], result['failed_symbols'])
        self.assertEqual([{'symbol': '005930', 'reason': 'BAD_PRICE', 'error': 'Broker current price missing or zero for 005930.'}], result['failed_details'])
        self.assertEqual([], collector.get_recent_bars('005930', 30))

