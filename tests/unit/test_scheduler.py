from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auto_trading.app.scheduler import SchedulerService
from auto_trading.common.trading_calendar import TradingCalendar
from auto_trading.strategy.models import Bar, MarketSnapshot, StrategyScore
from auto_trading.strategy.signals import SignalEngine
from auto_trading.universe.builder import UniverseItem


@dataclass(slots=True)
class _StubUniverseBuilder:
    symbols: list[str]
    rebuild_count: int = 0
    load_current_count: int = 0
    current_items: list[UniverseItem] = field(default_factory=list)

    def rebuild(self, as_of):
        self.rebuild_count += 1
        if not self.symbols:
            self.symbols = [f'{i:06d}' for i in range(12)]
        return [UniverseItem(symbol=symbol, name=f'Name {symbol}') for symbol in self.symbols]

    def load_current_universe(self) -> list[UniverseItem]:
        self.load_current_count += 1
        if self.current_items:
            self.symbols = [item.symbol for item in self.current_items]
        return list(self.current_items)


@dataclass(slots=True)
class _StubCollector:
    scores: dict[str, StrategyScore]
    latest_snapshots: dict[str, MarketSnapshot] = field(default_factory=dict)
    short_bar_symbols: set[str] = field(default_factory=set)
    refresh_statuses: dict[str, object] = field(default_factory=dict)
    refresh_summary: dict[str, object] = field(default_factory=lambda: {
        'snapshot_time': '2026-03-19T06:00:00+00:00',
        'requested_count': 0,
        'refreshed_count': 0,
        'failed_count': 0,
        'stale_symbol_count': 0,
        'latest_refresh_at': '2026-03-19T06:00:00+00:00',
        'failed_symbols': [],
        'stale_symbols': [],
    })

    def get_recent_bars(self, symbol: str, window: int) -> list[Bar]:
        score = self.scores.get(symbol)
        close_price = score.price if score is not None else 1000.0
        bar_count = 5 if symbol in self.short_bar_symbols else 30
        return [Bar(symbol=symbol, close=close_price, volume=1)] * bar_count


    def build_refresh_summary(self, symbols: list[str], *, stale_after_seconds: int, now=None) -> dict[str, object]:
        summary = dict(self.refresh_summary)
        summary.setdefault('requested_count', len(symbols))
        if summary.get('requested_count') == 0:
            summary['requested_count'] = len(symbols)
        return summary


    @property
    def cache(self):
        outer = self

        class _CacheView:
            def get_refresh_status(self, symbol: str):
                return outer.refresh_statuses.get(symbol)

        return _CacheView()

    def get_latest_snapshot(self, symbol: str) -> MarketSnapshot | None:
        return self.latest_snapshots.get(symbol)


@dataclass(slots=True)
class _StubScorer:
    scores: dict[str, StrategyScore]

    def score(self, bars: list[Bar]) -> StrategyScore:
        symbol = bars[-1].symbol
        return self.scores.get(symbol, StrategyScore(symbol=symbol, score_total=0, price=float(bars[-1].close)))


@dataclass(slots=True)
class _StubSignalEngine:
    exit_signals: dict[str, object] = field(default_factory=dict)
    entry_signals: list[object] = field(default_factory=list)

    def evaluate_entry(self, candidates: list[StrategyScore]) -> list[object]:
        return list(self.entry_signals)

    def evaluate_exit(self, position: object, snapshot: object) -> object | None:
        return self.exit_signals.get(getattr(position, 'symbol', ''))


@dataclass(slots=True)
class _StubPortfolioService:
    open_positions: list[object] = field(default_factory=list)
    sync_calls: int = 0

    def sync_from_broker(self) -> None:
        self.sync_calls += 1

    def snapshot(self):
        return type('Portfolio', (), {'open_positions': list(self.open_positions)})()


@dataclass(slots=True)
class _StubRiskEngine:
    exit_allowed: bool = False
    enter_allowed: bool = False
    enter_reason: str = 'max_positions'

    def can_enter(self, signal: object, portfolio: object):
        return type('Decision', (), {'allowed': self.enter_allowed, 'reason': self.enter_reason})()

    def can_exit(self, signal: object, portfolio: object):
        return type('Decision', (), {'allowed': self.exit_allowed})()

    def target_order_size(self, signal: object, portfolio: object):
        return None


@dataclass(slots=True)
class _StubOrderEngine:
    reconciled: int = 0
    exits: list[tuple[object, object]] = field(default_factory=list)
    entries: list[tuple[object, object]] = field(default_factory=list)
    entry_error: str | None = None
    orders_repository: object | None = None

    def reconcile_unknown_orders(self) -> None:
        self.reconciled += 1

    def submit_entry(self, signal: object, sizing: object) -> None:
        if self.entry_error:
            raise RuntimeError(self.entry_error)
        self.entries.append((signal, sizing))

    def submit_exit(self, signal: object, position: object) -> None:
        self.exits.append((signal, position))


@dataclass(slots=True)
class _StubRecoveryService:
    def recover(self) -> None:
        return None


@dataclass(slots=True)
class _StubFailSafeMonitor:
    def should_block_new_orders(self) -> bool:
        return False


@dataclass(slots=True)
class _StubNotifier:
    payloads: list[dict[str, object]] = field(default_factory=list)
    daily_reports: list[dict[str, object]] = field(default_factory=list)
    system_events: list[dict[str, object]] = field(default_factory=list)

    def send_target_scores(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)

    def send_daily_report(self, payload: dict[str, object]) -> None:
        self.daily_reports.append(payload)

    def send_system_event(self, payload: dict[str, object]) -> None:
        self.system_events.append(payload)


@dataclass(slots=True)
class _StubSystemEventsRepository:
    events: list[dict[str, object]] = field(default_factory=list)

    def create(
        self,
        event_type: str,
        severity: str,
        component: str,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> int:
        self.events.append(
            {
                'event_type': event_type,
                'severity': severity,
                'component': component,
                'message': message,
                'payload': payload or {},
            }
        )
        return len(self.events)

    def exists_for_report_date(self, event_type: str, report_date: str) -> bool:
        for event in self.events:
            if event.get('event_type') != event_type:
                continue
            payload = event.get('payload') or {}
            if str(payload.get('report_date', '')) == report_date:
                return True
        return False

    def exists_recent_event(self, event_type: str, *, within_seconds: int) -> bool:
        for event in reversed(self.events):
            if event.get('event_type') == event_type:
                return True
        return False

    def exists_recent_event_for_symbol(self, event_type: str, symbol: str, *, within_seconds: int) -> bool:
        for event in reversed(self.events):
            if event.get('event_type') != event_type:
                continue
            payload = event.get('payload') or {}
            if str(payload.get('symbol', '')) == symbol:
                return True
        return False


class SchedulerTargetsTest(unittest.TestCase):
    def _fresh_status(self, timestamp: str = '2999-03-19T06:00:00+00:00') -> object:
        return type('RefreshStatus', (), {'last_success_at': timestamp, 'last_failure_at': '', 'source': 'REST'})()

    def _utc_iso_now(self, *, minutes_offset: int = 0, seconds_offset: int = 0) -> str:
        return (datetime.now(timezone.utc) + timedelta(minutes=minutes_offset, seconds=seconds_offset)).isoformat()

    def _calendar(self) -> TradingCalendar:
        return TradingCalendar(Path('data/krx_holidays.csv'))

    def _build_scores(self) -> dict[str, StrategyScore]:
        return {
            f'{i:06d}': StrategyScore(symbol=f'{i:06d}', score_total=100 - i, price=1000 + i)
            for i in range(12)
        }

    def test_run_market_scan_sends_top_10_candidate_scores_only_on_change(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        universe_builder = _StubUniverseBuilder(symbols=list(scores.keys()))
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
        )
        scheduler.run_market_scan()
        scheduler.run_market_scan()
        self.assertEqual(1, len(notifier.payloads))
        payload = notifier.payloads[0]
        self.assertEqual(10, len(payload['items']))
        self.assertEqual('000000', payload['items'][0]['symbol'])
        self.assertEqual(100, payload['items'][0]['score_total'])
        self.assertEqual('000009', payload['items'][-1]['symbol'])

        scores['000000'] = StrategyScore(symbol='000000', score_total=101, price=1000)
        scheduler.run_market_scan()
        self.assertEqual(2, len(notifier.payloads))
        self.assertEqual(101, notifier.payloads[-1]['items'][0]['score_total'])
        self.assertEqual(0, universe_builder.rebuild_count)
        self.assertEqual(0, universe_builder.load_current_count)

    def test_run_market_scan_excludes_price_below_ma5_from_target_alerts(self) -> None:
        scores = {
            '000000': StrategyScore(symbol='000000', score_total=100, price=90.0, ma5=100.0),
            '000001': StrategyScore(symbol='000001', score_total=95, price=110.0, ma5=100.0),
        }
        notifier = _StubNotifier()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000', '000001']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
        )
        scheduler.run_market_scan()
        self.assertEqual(1, len(notifier.payloads))
        self.assertEqual(['000001'], [item['symbol'] for item in notifier.payloads[0]['items']])

    def test_run_market_scan_cools_down_exit_when_open_sell_order_exists(self) -> None:
        scores = self._build_scores()
        position = type('Position', (), {'symbol': '006360', 'qty': 10, 'avg_entry_price': 100.0, 'current_price': 120.0, 'status': 'OPEN'})()
        exit_signal = type('ExitSignal', (), {'reason': 'take_profit', 'order_type': 'LIMIT', 'price': 120.0})()

        class _OrdersRepo:
            def find_open_for_symbol(self, symbol):
                return [type('Order', (), {'side': 'SELL'})()]
            def has_recent_rejected_exit(self, symbol, *, within_seconds):
                return False

        order_engine = _StubOrderEngine(orders_repository=_OrdersRepo())
        system_events = _StubSystemEventsRepository()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['006360']),
            market_data_collector=_StubCollector(
                scores=scores,
                latest_snapshots={'006360': MarketSnapshot(symbol='006360', price=120.0)},
            ),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(exit_signals={'006360': exit_signal}),
            portfolio_service=_StubPortfolioService(open_positions=[position]),
            risk_engine=_StubRiskEngine(exit_allowed=True),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events,
        )

        scheduler.run_market_scan()

        self.assertEqual([], order_engine.exits)
        self.assertTrue(any(event['event_type'] == 'exit_retry_cooled_down' for event in system_events.events))

    def test_run_market_scan_excludes_already_held_symbols_from_target_alerts(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        universe_builder = _StubUniverseBuilder(symbols=['000000', '000001', '000002'])
        portfolio_service = _StubPortfolioService(
            open_positions=[type('Position', (), {'symbol': '000000'})(), type('Position', (), {'symbol': '000001'})()]
        )
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=portfolio_service,
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
        )
        scheduler.run_market_scan()
        self.assertEqual(1, len(notifier.payloads))
        self.assertEqual(['000002'], [item['symbol'] for item in notifier.payloads[0]['items']])

    def test_run_market_scan_skips_target_alert_when_all_candidates_are_already_held(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        universe_builder = _StubUniverseBuilder(symbols=['000000', '000001'])
        portfolio_service = _StubPortfolioService(
            open_positions=[type('Position', (), {'symbol': '000000'})(), type('Position', (), {'symbol': '000001'})()]
        )
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=portfolio_service,
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
        )
        scheduler.run_market_scan()
        self.assertEqual(0, len(notifier.payloads))

    def test_run_market_scan_loads_current_universe_before_rebuild(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        refreshed: list[list[str]] = []
        current_items = [UniverseItem(symbol=f'{i:06d}', name=f'Name {i:06d}') for i in range(12)]
        universe_builder = _StubUniverseBuilder(symbols=[], current_items=current_items)
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
            market_data_refresher=lambda request: refreshed.append(request),
        )
        scheduler.run_market_scan()
        self.assertEqual(1, universe_builder.load_current_count)
        self.assertEqual(0, universe_builder.rebuild_count)
        self.assertEqual(1, len(refreshed))
        self.assertEqual(12, len(refreshed[-1]['requested_symbols']))
        self.assertEqual(1, len(notifier.payloads))

    def test_run_market_scan_refreshes_rest_data_for_union_of_universe_and_open_positions(self) -> None:
        scores = self._build_scores()
        refreshed: list[list[str]] = []
        universe_builder = _StubUniverseBuilder(symbols=['000000', '000001'])
        portfolio_service = _StubPortfolioService(
            open_positions=[type('Position', (), {'symbol': '088350'})()]
        )
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=portfolio_service,
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            market_data_refresher=lambda request: refreshed.append(request),
        )
        scheduler.run_market_scan()
        self.assertEqual(1, len(refreshed))
        self.assertEqual(['088350'], refreshed[0]['priority_symbols'])
        self.assertEqual(['000000', '000001'], refreshed[0]['scan_symbols'])
        self.assertEqual(['088350', '000000', '000001'], refreshed[0]['requested_symbols'])

    def test_run_pre_market_refreshes_rest_data_for_union_of_universe_and_open_positions(self) -> None:
        scores = self._build_scores()
        refreshed: list[list[str]] = []
        current_items = [UniverseItem(symbol='000000', name='Name 000000')]
        universe_builder = _StubUniverseBuilder(symbols=['000000'], current_items=current_items)
        portfolio_service = _StubPortfolioService(
            open_positions=[type('Position', (), {'symbol': '088350'})()]
        )
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=portfolio_service,
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            market_data_refresher=lambda request: refreshed.append(request),
        )
        scheduler.run_pre_market()
        self.assertEqual(1, len(refreshed))
        self.assertEqual(['088350'], refreshed[0]['priority_symbols'])
        self.assertEqual(['000000'], refreshed[0]['scan_symbols'])
        self.assertEqual(['088350', '000000'], refreshed[0]['requested_symbols'])

    def test_run_market_scan_rebuilds_when_current_universe_missing(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        refreshed: list[list[str]] = []
        universe_builder = _StubUniverseBuilder(symbols=[])
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
            market_data_refresher=lambda request: refreshed.append(request),
        )
        scheduler.run_market_scan()
        self.assertEqual(1, universe_builder.load_current_count)
        self.assertEqual(1, universe_builder.rebuild_count)
        self.assertEqual(1, len(refreshed))
        self.assertEqual(12, len(refreshed[-1]['requested_symbols']))
        self.assertEqual(1, len(notifier.payloads))

    def test_run_market_scan_records_universe_restore_failure(self) -> None:
        class _FailingUniverseBuilder(_StubUniverseBuilder):
            def load_current_universe(self) -> list[UniverseItem]:
                self.load_current_count += 1
                raise RuntimeError('load failed')

        scores = self._build_scores()
        universe_builder = _FailingUniverseBuilder(symbols=[])
        system_events_repository = _StubSystemEventsRepository()
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
        )
        scheduler.run_market_scan()
        failure_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'market_universe_restore_failed'
        ]
        self.assertEqual(1, len(failure_events))
        self.assertEqual('ERROR', failure_events[0]['severity'])
        self.assertEqual('load failed', failure_events[0]['payload']['error'])

    def test_run_pre_market_restores_cached_universe_when_rebuild_is_empty(self) -> None:
        current_items = [UniverseItem(symbol=f'{i:06d}', name=f'Name {i:06d}') for i in range(3)]

        class _EmptyRebuildUniverseBuilder(_StubUniverseBuilder):
            def rebuild(self, as_of):
                self.rebuild_count += 1
                self.symbols = []
                return []

        refreshed: list[list[str]] = []
        system_events_repository = _StubSystemEventsRepository()
        universe_builder = _EmptyRebuildUniverseBuilder(symbols=[], current_items=current_items)
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=self._build_scores()),
            strategy_scorer=_StubScorer(scores=self._build_scores()),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            market_data_refresher=lambda request: refreshed.append(request),
        )
        scheduler.run_pre_market()
        self.assertEqual(1, universe_builder.rebuild_count)
        self.assertEqual(1, universe_builder.load_current_count)
        self.assertEqual([item.symbol for item in current_items], universe_builder.symbols)
        self.assertEqual(1, len(refreshed))
        self.assertEqual([], refreshed[0]['priority_symbols'])
        self.assertEqual([item.symbol for item in current_items], refreshed[0]['scan_symbols'])
        self.assertEqual([item.symbol for item in current_items], refreshed[0]['requested_symbols'])
        warning_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'market_universe_rebuild_empty'
        ]
        self.assertEqual(1, len(warning_events))

    def test_tick_sends_daily_report_once_in_post_market(self) -> None:
        notifier = _StubNotifier()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=[]),
            market_data_collector=_StubCollector(scores=self._build_scores()),
            strategy_scorer=_StubScorer(scores=self._build_scores()),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
            daily_report_builder=lambda: {'report_date': '2026-03-16', 'message': '[AUTO_TRADING] 일일 리포트'},
        )
        scheduler.tick(__import__('datetime').datetime(2026, 3, 16, 15, 30))
        scheduler.tick(__import__('datetime').datetime(2026, 3, 16, 15, 31))
        self.assertEqual(1, len(notifier.daily_reports))

    def test_tick_skips_daily_report_when_report_date_already_sent(self) -> None:
        notifier = _StubNotifier()
        system_events_repository = _StubSystemEventsRepository(
            events=[
                {
                    'event_type': 'daily_report_notification_sent',
                    'severity': 'INFO',
                    'component': 'telegram',
                    'message': '[AUTO_TRADING] 일일 리포트',
                    'payload': {'report_date': '2026-03-16'},
                }
            ]
        )
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=[]),
            market_data_collector=_StubCollector(scores=self._build_scores()),
            strategy_scorer=_StubScorer(scores=self._build_scores()),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
            system_events_repository=system_events_repository,
            daily_report_builder=lambda: {'report_date': '2026-03-16', 'message': '[AUTO_TRADING] 일일 리포트'},
        )
        scheduler.tick(__import__('datetime').datetime(2026, 3, 16, 15, 30))
        self.assertEqual(0, len(notifier.daily_reports))
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'daily_report_duplicate_skipped'
        ]
        self.assertEqual(1, len(skipped_events))


    def test_run_market_scan_blocks_price_exit_when_snapshot_is_stale(self) -> None:
        scores = self._build_scores()
        stale_status = type('RefreshStatus', (), {'last_success_at': '2026-03-19T00:00:00+00:00', 'source': 'REST'})()
        collector = _StubCollector(
            scores=scores,
            latest_snapshots={'088350': MarketSnapshot(symbol='088350', price=5210.0, source='REST', refreshed_at='2026-03-19T00:00:00+00:00')},
            short_bar_symbols={'088350'},
            refresh_statuses={'088350': stale_status},
        )
        signal_engine = SignalEngine()
        order_engine = _StubOrderEngine()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=collector,
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(
                open_positions=[type('Position', (), {'symbol': '088350', 'avg_entry_price': 4735.0, 'current_price': 5210.0, 'opened_at': '2026-03-18T09:00:00+09:00'})()]
            ),
            risk_engine=_StubRiskEngine(exit_allowed=True),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            market_data_stale_after_seconds=120,
        )
        scheduler.run_market_scan()
        self.assertEqual(0, len(order_engine.exits))

    def test_run_market_scan_exits_open_position_from_latest_snapshot_without_20_bars(self) -> None:
        scores = self._build_scores()
        fresh_status = type('RefreshStatus', (), {'last_success_at': '2999-03-19T06:00:00+00:00', 'source': 'REST'})()
        collector = _StubCollector(
            scores=scores,
            latest_snapshots={'088350': MarketSnapshot(symbol='088350', price=5210.0, source='REST', refreshed_at='2999-03-19T06:00:00+00:00')},
            short_bar_symbols={'088350'},
            refresh_statuses={'088350': fresh_status},
        )
        signal_engine = SignalEngine()
        order_engine = _StubOrderEngine()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=collector,
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(
                open_positions=[type('Position', (), {'symbol': '088350', 'avg_entry_price': 4735.0, 'current_price': 5210.0})()]
            ),
            risk_engine=_StubRiskEngine(exit_allowed=True),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
        )
        scheduler.run_market_scan()
        self.assertEqual(1, len(order_engine.exits))
        self.assertEqual('088350', order_engine.exits[0][0].symbol)

    def test_run_market_scan_records_entry_skipped_when_risk_denies_entry(self) -> None:
        scores = self._build_scores()
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '000000', 'score_total': 100, 'price': 1000.0})()]
        )
        system_events_repository = _StubSystemEventsRepository()
        order_engine = _StubOrderEngine()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=False, enter_reason='max_positions'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
        )
        scheduler.run_market_scan()
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_skipped'
        ]
        self.assertEqual(1, len(skipped_events))
        self.assertEqual('000000', skipped_events[0]['payload']['symbol'])
        self.assertEqual('max_positions', skipped_events[0]['payload']['reason'])
        self.assertEqual(0, len(order_engine.entries))

    def test_run_market_scan_blocks_entry_when_market_data_is_stale(self) -> None:
        scores = self._build_scores()
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '000000', 'score_total': 100, 'price': 1000.0})()]
        )
        system_events_repository = _StubSystemEventsRepository()
        order_engine = _StubOrderEngine()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(
                scores=scores,
                refresh_statuses={'000000': self._fresh_status('2026-03-19T00:00:00+00:00')},
            ),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=True, enter_reason='ok'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            market_data_stale_after_seconds=120,
        )
        scheduler.run_market_scan()
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_skipped'
        ]
        self.assertEqual(1, len(skipped_events))
        self.assertEqual('stale_market_data', skipped_events[0]['payload']['reason'])
        self.assertEqual(0, len(order_engine.entries))

    def test_run_market_scan_blocks_entry_when_same_symbol_position_sync_is_unstable(self) -> None:
        scores = self._build_scores()
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '000000', 'score_total': 100, 'price': 1000.0})()]
        )
        system_events_repository = _StubSystemEventsRepository(
            events=[
                {
                    'event_type': 'position_mismatch',
                    'severity': 'WARN',
                    'component': 'portfolio.sync',
                    'message': 'Broker holdings did not include a locally tracked active position during sync. Keeping local position for retry.',
                    'payload': {'symbol': '000000'},
                }
            ]
        )
        order_engine = _StubOrderEngine()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=True, enter_reason='ok'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            entry_pause_after_position_mismatch_seconds=180,
        )
        scheduler.run_market_scan()
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_skipped'
        ]
        self.assertEqual(1, len(skipped_events))
        self.assertEqual('position_sync_unstable', skipped_events[0]['payload']['reason'])
        self.assertEqual(0, len(order_engine.entries))

    def test_run_market_scan_does_not_block_entry_for_other_symbol_position_mismatch(self) -> None:
        scores = self._build_scores()
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '000000', 'score_total': 100, 'price': 1000.0})()]
        )
        system_events_repository = _StubSystemEventsRepository(
            events=[
                {
                    'event_type': 'position_mismatch',
                    'severity': 'WARN',
                    'component': 'portfolio.sync',
                    'message': 'Broker holdings did not include a locally tracked active position during sync. Keeping local position for retry.',
                    'payload': {'symbol': '004000'},
                }
            ]
        )
        order_engine = _StubOrderEngine()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=True, enter_reason='ok'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            entry_pause_after_position_mismatch_seconds=180,
        )
        scheduler.run_market_scan()
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_skipped'
        ]
        self.assertEqual(0, len(skipped_events))
        self.assertEqual(1, len(order_engine.entries))

    def test_run_market_scan_blocks_reentry_during_ma5_breakdown_cooldown(self) -> None:
        scores = self._build_scores()
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '000000', 'score_total': 100, 'price': 1000.0})()]
        )

        class _OrdersRepo:
            def has_filled_exit_intent_for_symbol_today(self, symbol, intent):
                return symbol == '000000' and intent == 'MA5_BREAKDOWN'

            def find_latest_filled_exit_intent_at(self, symbol, intent):
                if symbol == '000000' and intent == 'MA5_BREAKDOWN':
                    return self_ref._utc_iso_now(minutes_offset=-10)
                return ''

        self_ref = self
        order_engine = _StubOrderEngine(orders_repository=_OrdersRepo())
        system_events_repository = _StubSystemEventsRepository()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=True, enter_reason='ok'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            ma5_reentry_cooldown_seconds=2700,
        )
        scheduler.run_market_scan()
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_skipped'
        ]
        self.assertEqual(1, len(skipped_events))
        self.assertEqual('recent_ma5_breakdown_exit', skipped_events[0]['payload']['reason'])
        self.assertEqual(0, len(order_engine.entries))

    def test_run_market_scan_blocks_reentry_until_ma5_recovery_is_confirmed_twice(self) -> None:
        scores = {
            '000000': StrategyScore(symbol='000000', score_total=100, price=1000.0, ma5=990.0),
        }
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '000000', 'score_total': 100, 'price': 1000.0})()]
        )

        latest_exit_at = self._utc_iso_now(minutes_offset=-50)

        class _OrdersRepo:
            def has_filled_exit_intent_for_symbol_today(self, symbol, intent):
                return symbol == '000000' and intent == 'MA5_BREAKDOWN'

            def find_latest_filled_exit_intent_at(self, symbol, intent):
                if symbol == '000000' and intent == 'MA5_BREAKDOWN':
                    return latest_exit_at
                return ''

        order_engine = _StubOrderEngine(orders_repository=_OrdersRepo())
        system_events_repository = _StubSystemEventsRepository()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=True, enter_reason='ok'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            ma5_reentry_cooldown_seconds=2700,
            ma5_reentry_recovery_confirmations=2,
        )
        scheduler.run_market_scan()
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_skipped'
        ]
        self.assertEqual(1, len(skipped_events))
        self.assertEqual('ma5_recovery_unconfirmed', skipped_events[0]['payload']['reason'])
        self.assertEqual(0, len(order_engine.entries))

    def test_run_market_scan_allows_reentry_after_cooldown_and_second_ma5_recovery_confirmation(self) -> None:
        scores = {
            '000000': StrategyScore(symbol='000000', score_total=100, price=1000.0, ma5=990.0),
        }
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '000000', 'score_total': 100, 'price': 1000.0})()]
        )

        latest_exit_at = self._utc_iso_now(minutes_offset=-50)

        class _OrdersRepo:
            def has_filled_exit_intent_for_symbol_today(self, symbol, intent):
                return symbol == '000000' and intent == 'MA5_BREAKDOWN'

            def find_latest_filled_exit_intent_at(self, symbol, intent):
                if symbol == '000000' and intent == 'MA5_BREAKDOWN':
                    return latest_exit_at
                return ''

        order_engine = _StubOrderEngine(orders_repository=_OrdersRepo())
        system_events_repository = _StubSystemEventsRepository()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=True, enter_reason='ok'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            ma5_reentry_cooldown_seconds=2700,
            ma5_reentry_recovery_confirmations=2,
        )
        scheduler.run_market_scan()
        scheduler.run_market_scan()
        skipped_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_skipped'
        ]
        self.assertEqual(1, len(skipped_events))
        self.assertEqual('ma5_recovery_unconfirmed', skipped_events[0]['payload']['reason'])
        self.assertEqual(1, len(order_engine.entries))

    def test_run_market_scan_records_entry_submit_failure_without_crashing(self) -> None:
        scores = self._build_scores()
        signal_engine = _StubSignalEngine(
            entry_signals=[type('EntrySignal', (), {'symbol': '004000', 'score_total': 95, 'price': 1000.0})()]
        )
        system_events_repository = _StubSystemEventsRepository()
        order_engine = _StubOrderEngine(entry_error='Active position already exists for 004000.')
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'004000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(enter_allowed=True, enter_reason='ok'),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
        )
        scheduler.run_market_scan()
        failure_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'entry_submit_failed'
        ]
        self.assertEqual(1, len(failure_events))
        self.assertEqual('004000', failure_events[0]['payload']['symbol'])
        self.assertIn('Active position already exists', failure_events[0]['payload']['reason'])


    def test_market_data_refresh_request_prioritizes_open_positions(self) -> None:
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000', '000001', '088350']),
            market_data_collector=_StubCollector(scores=self._build_scores()),
            strategy_scorer=_StubScorer(scores=self._build_scores()),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(
                open_positions=[
                    type('Position', (), {'symbol': '088350'})(),
                    type('Position', (), {'symbol': '005930'})(),
                ]
            ),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
        )
        request = scheduler._build_market_data_refresh_request(
            ['000000', '000001', '088350'],
            scheduler.portfolio_service.snapshot(),
        )
        self.assertEqual(['088350', '005930'], request['priority_symbols'])
        self.assertEqual(['000000', '000001'], request['scan_symbols'])
        self.assertEqual(['088350', '005930', '000000', '000001'], request['requested_symbols'])

    def test_run_market_scan_reconciles_orders_and_syncs_portfolio_before_scan(self) -> None:
        scores = self._build_scores()
        portfolio_service = _StubPortfolioService()
        order_engine = _StubOrderEngine()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000', '000001']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=portfolio_service,
            risk_engine=_StubRiskEngine(),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            market_data_refresher=lambda request: request,
        )
        scheduler.run_market_scan()
        self.assertEqual(1, order_engine.reconciled)
        self.assertEqual(1, portfolio_service.sync_calls)

    def test_run_market_scan_skips_exit_retry_after_recent_rejected_sell(self) -> None:
        scores = self._build_scores()
        fresh_status = type('RefreshStatus', (), {'last_success_at': '2999-03-19T06:00:00+00:00', 'source': 'REST'})()
        collector = _StubCollector(
            scores=scores,
            latest_snapshots={'006360': MarketSnapshot(symbol='006360', price=30600.0, source='REST', refreshed_at='2999-03-19T06:00:00+00:00')},
            short_bar_symbols={'006360'},
            refresh_statuses={'006360': fresh_status},
        )
        signal_engine = SignalEngine()
        order_engine = _StubOrderEngine(
            orders_repository=type(
                'OrdersRepo',
                (),
                {'has_recent_rejected_exit': staticmethod(lambda symbol, *, within_seconds: symbol == '006360')},
            )(),
        )
        system_events_repository = _StubSystemEventsRepository()
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=collector,
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=signal_engine,
            portfolio_service=_StubPortfolioService(
                open_positions=[type('Position', (), {'symbol': '006360', 'avg_entry_price': 26250.0, 'current_price': 30600.0})()]
            ),
            risk_engine=_StubRiskEngine(exit_allowed=True),
            order_engine=order_engine,
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
        )
        scheduler.run_market_scan()
        self.assertEqual(0, len(order_engine.exits))
        cooled_down_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'exit_retry_cooled_down'
        ]
        self.assertEqual(1, len(cooled_down_events))

    def test_run_market_scan_uses_rest_market_data_refresher(self) -> None:
        scores = self._build_scores()
        refreshed: list[list[str]] = []
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000', '000001']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            market_data_refresher=lambda request: refreshed.append(request),
        )
        scheduler.run_market_scan()
        self.assertEqual(['000000', '000001'], refreshed[0]['scan_symbols'])
        self.assertEqual([], refreshed[0]['priority_symbols'])
        self.assertEqual(['000000', '000001'], refreshed[0]['requested_symbols'])


    def test_run_market_scan_sends_market_data_degraded_alert(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        system_events_repository = _StubSystemEventsRepository()
        collector = _StubCollector(
            scores=scores,
            refresh_summary={
                'snapshot_time': '2026-03-19T06:14:49+00:00',
                'requested_count': 3,
                'attempted_count': 3,
                'refreshed_count': 2,
                'skipped_count': 0,
                'priority_count': 1,
                'failed_count': 1,
                'stale_symbol_count': 1,
                'latest_refresh_at': '2026-03-19T06:14:48+00:00',
                'failed_symbols': ['088350'],
                'stale_symbols': ['088350'],
            },
        )
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000', '000001']),
            market_data_collector=collector,
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            notifier=notifier,
            system_events_repository=system_events_repository,
            market_data_refresher=lambda request: {'attempted_count': 3, 'skipped_count': 0, 'priority_count': 1},
        )
        scheduler.run_market_scan()
        self.assertEqual(0, len(notifier.system_events))
        degraded_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'market_data_refresh_degraded'
        ]
        self.assertEqual(1, len(degraded_events))

    def test_run_market_scan_records_market_data_refresh_summary(self) -> None:
        scores = self._build_scores()
        system_events_repository = _StubSystemEventsRepository()
        collector = _StubCollector(
            scores=scores,
            refresh_summary={
                'snapshot_time': '2026-03-19T06:14:49+00:00',
                'requested_count': 3,
                'refreshed_count': 2,
                'failed_count': 1,
                'stale_symbol_count': 1,
                'latest_refresh_at': '2026-03-19T06:14:48+00:00',
                'failed_symbols': ['088350'],
                'stale_symbols': ['088350'],
            },
        )
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000', '000001']),
            market_data_collector=collector,
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            market_data_refresher=lambda request: {'attempted_count': 2, 'skipped_count': 1, 'priority_count': 0},
        )
        scheduler.run_market_scan()
        refresh_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'market_data_refresh_summary'
        ]
        self.assertEqual(1, len(refresh_events))
        self.assertEqual(3, refresh_events[0]['payload']['requested_count'])
        self.assertEqual(1, refresh_events[0]['payload']['failed_count'])
        self.assertEqual(2, refresh_events[0]['payload']['attempted_count'])
        self.assertEqual(1, refresh_events[0]['payload']['skipped_count'])
        self.assertEqual(['088350'], refresh_events[0]['payload']['stale_symbols'])

    def test_run_market_scan_records_market_data_refresh_failure_without_crashing(self) -> None:
        scores = self._build_scores()
        system_events_repository = _StubSystemEventsRepository()

        def _failing_refresh(symbols: list[str]) -> None:
            raise ConnectionAbortedError('socket is already closed')

        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000']),
            market_data_collector=_StubCollector(scores=scores, refresh_statuses={'000000': self._fresh_status()}),
            strategy_scorer=_StubScorer(scores=scores),
            signal_engine=_StubSignalEngine(),
            portfolio_service=_StubPortfolioService(),
            risk_engine=_StubRiskEngine(),
            order_engine=_StubOrderEngine(),
            recovery_service=_StubRecoveryService(),
            fail_safe_monitor=_StubFailSafeMonitor(),
            trading_calendar=self._calendar(),
            system_events_repository=system_events_repository,
            market_data_refresher=_failing_refresh,
        )
        scheduler.run_market_scan()
        failure_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'market_data_refresh_failed'
        ]
        self.assertEqual(1, len(failure_events))
        self.assertIn('socket is already closed', failure_events[0]['payload']['error'])


if __name__ == '__main__':
    unittest.main()



