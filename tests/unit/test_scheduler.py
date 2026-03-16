from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path

from auto_trading.app.scheduler import SchedulerService
from auto_trading.common.trading_calendar import TradingCalendar
from auto_trading.strategy.models import Bar, StrategyScore
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

    def get_recent_bars(self, symbol: str, window: int) -> list[Bar]:
        score = self.scores[symbol]
        return [Bar(symbol=symbol, close=score.price, volume=1)] * 30


@dataclass(slots=True)
class _StubScorer:
    scores: dict[str, StrategyScore]

    def score(self, bars: list[Bar]) -> StrategyScore:
        return self.scores[bars[-1].symbol]


@dataclass(slots=True)
class _StubSignalEngine:
    def evaluate_entry(self, candidates: list[StrategyScore]) -> list[object]:
        return []

    def evaluate_exit(self, position: object, snapshot: object) -> object | None:
        return None


@dataclass(slots=True)
class _StubPortfolioService:
    def snapshot(self):
        return type('Portfolio', (), {'open_positions': []})()


@dataclass(slots=True)
class _StubRiskEngine:
    def can_enter(self, signal: object, portfolio: object):
        return type('Decision', (), {'allowed': False})()

    def can_exit(self, signal: object, portfolio: object):
        return type('Decision', (), {'allowed': False})()

    def target_order_size(self, signal: object, portfolio: object):
        return None


@dataclass(slots=True)
class _StubOrderEngine:
    reconciled: int = 0

    def reconcile_unknown_orders(self) -> None:
        self.reconciled += 1

    def submit_entry(self, signal: object, sizing: object) -> None:
        return None

    def submit_exit(self, signal: object, position: object) -> None:
        return None


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

    def send_target_scores(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)


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
        subscribed: list[list[str]] = []
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
            quote_subscription_updater=subscribed.append,
        )
        scheduler.run_market_scan()
        self.assertEqual(1, universe_builder.load_current_count)
        self.assertEqual(0, universe_builder.rebuild_count)
        self.assertEqual(1, len(subscribed))
        self.assertEqual(12, len(subscribed[0]))
        self.assertEqual(1, len(notifier.payloads))

    def test_run_market_scan_rebuilds_when_current_universe_missing(self) -> None:
        scores = self._build_scores()
        notifier = _StubNotifier()
        subscribed: list[list[str]] = []
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
            quote_subscription_updater=subscribed.append,
        )
        scheduler.run_market_scan()
        self.assertEqual(1, universe_builder.load_current_count)
        self.assertEqual(1, universe_builder.rebuild_count)
        self.assertEqual(1, len(subscribed))
        self.assertEqual(12, len(subscribed[0]))
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

        subscribed: list[list[str]] = []
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
            quote_subscription_updater=subscribed.append,
        )
        scheduler.run_pre_market()
        self.assertEqual(1, universe_builder.rebuild_count)
        self.assertEqual(1, universe_builder.load_current_count)
        self.assertEqual([item.symbol for item in current_items], universe_builder.symbols)
        self.assertEqual(1, len(subscribed))
        self.assertEqual([item.symbol for item in current_items], subscribed[0])
        warning_events = [
            event for event in system_events_repository.events
            if event['event_type'] == 'market_universe_rebuild_empty'
        ]
        self.assertEqual(1, len(warning_events))


if __name__ == '__main__':
    unittest.main()
