from __future__ import annotations

import unittest
from dataclasses import dataclass, field
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

    def reconcile_unknown_orders(self) -> None:
        self.reconciled += 1

    def submit_entry(self, signal: object, sizing: object) -> None:
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

    def send_target_scores(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)

    def send_daily_report(self, payload: dict[str, object]) -> None:
        self.daily_reports.append(payload)


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


class SchedulerTargetsTest(unittest.TestCase):
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
            market_data_collector=_StubCollector(scores=scores),
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

    def test_run_market_scan_loads_current_universe_before_rebuild(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        refreshed: list[list[str]] = []
        current_items = [UniverseItem(symbol=f'{i:06d}', name=f'Name {i:06d}') for i in range(12)]
        universe_builder = _StubUniverseBuilder(symbols=[], current_items=current_items)
        scheduler = SchedulerService(
            universe_builder=universe_builder,
            market_data_collector=_StubCollector(scores=scores),
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
            market_data_collector=_StubCollector(scores=scores),
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
            market_data_collector=_StubCollector(scores=scores),
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
            market_data_collector=_StubCollector(scores=scores),
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
            market_data_collector=_StubCollector(scores=scores),
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
            daily_report_builder=lambda: {'message': '[AUTO_TRADING] 일일 리포트'},
        )
        scheduler.tick(__import__('datetime').datetime(2026, 3, 16, 15, 30))
        scheduler.tick(__import__('datetime').datetime(2026, 3, 16, 15, 31))
        self.assertEqual(1, len(notifier.daily_reports))


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
            market_data_collector=_StubCollector(scores=scores),
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

    def test_run_market_scan_uses_rest_market_data_refresher(self) -> None:
        scores = self._build_scores()
        refreshed: list[list[str]] = []
        scheduler = SchedulerService(
            universe_builder=_StubUniverseBuilder(symbols=['000000', '000001']),
            market_data_collector=_StubCollector(scores=scores),
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
            market_data_collector=_StubCollector(scores=scores),
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
