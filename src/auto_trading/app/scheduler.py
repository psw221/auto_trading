from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from time import sleep

from auto_trading.common.trading_calendar import TradingCalendar
from auto_trading.failsafe.monitor import FailSafeMonitor
from auto_trading.market_data.collector import MarketDataCollector
from auto_trading.orders.engine import OrderEngine
from auto_trading.portfolio.service import PortfolioService
from auto_trading.risk.engine import RiskEngine
from auto_trading.strategy.models import MarketSnapshot
from auto_trading.strategy.scorer import StrategyScorer
from auto_trading.strategy.signals import SignalEngine
from auto_trading.universe.builder import UniverseBuilder


@dataclass(slots=True)
class SchedulerService:
    universe_builder: UniverseBuilder
    market_data_collector: MarketDataCollector
    strategy_scorer: StrategyScorer
    signal_engine: SignalEngine
    portfolio_service: PortfolioService
    risk_engine: RiskEngine
    order_engine: OrderEngine
    recovery_service: object
    fail_safe_monitor: FailSafeMonitor
    trading_calendar: TradingCalendar
    notifier: object | None = None
    system_events_repository: object | None = None
    strategy_snapshots_repository: object | None = None
    universe_master_refresher: object | None = None
    holiday_calendar_refresher: object | None = None
    daily_report_builder: object | None = None
    market_data_refresher: object | None = None
    market_scan_interval_seconds: float = 30.0
    loop_sleep_seconds: float = 1.0
    _last_pre_market_run_date: str | None = field(init=False, default=None)
    _last_post_market_run_date: str | None = field(init=False, default=None)
    _last_market_scan_at: datetime | None = field(init=False, default=None)
    _last_target_scores_signature: tuple[tuple[str, int, float], ...] = field(init=False, default_factory=tuple)

    def run_forever(self) -> None:
        while True:
            self.tick()
            sleep(self.loop_sleep_seconds)

    def tick(self, now: datetime | None = None) -> None:
        current = now or datetime.now()
        if not self.trading_calendar.is_trading_day(current):
            return
        if self._is_pre_market(current):
            self._run_pre_market_once(current)
        elif self._is_market_open(current):
            self._run_market_cycle(current)
        elif self._is_post_market(current):
            self._run_post_market_once(current)

    def run_pre_market(self) -> None:
        self._refresh_holiday_calendar()
        if not self.trading_calendar.is_trading_day(datetime.now()):
            return
        self._refresh_universe_master()
        self.recovery_service.recover()
        items = self._rebuild_or_restore_market_universe()
        portfolio = self.portfolio_service.snapshot()
        self._refresh_market_data(
            self._build_quote_subscription_symbols([item.symbol for item in items], portfolio)
        )

    def run_market_scan(self) -> None:
        if not self.trading_calendar.is_trading_day(datetime.now()):
            return
        if self.fail_safe_monitor.should_block_new_orders():
            self.order_engine.reconcile_unknown_orders()
            return
        self._ensure_market_universe_ready()
        if not self.universe_builder.symbols:
            self._record_market_scan_summary(
                universe_count=0,
                scored_count=0,
                qualified_count=0,
                top_candidate_count=0,
            )
            return

        portfolio = self.portfolio_service.snapshot()
        refresh_symbols = self._build_quote_subscription_symbols(self.universe_builder.symbols, portfolio)
        self._refresh_market_data(refresh_symbols)
        for position in portfolio.open_positions:
            snapshot = self._build_position_exit_snapshot(position)
            if snapshot is None:
                continue
            exit_signal = self.signal_engine.evaluate_exit(position, snapshot)
            if exit_signal is None:
                continue
            decision = self.risk_engine.can_exit(exit_signal, portfolio)
            if decision.allowed:
                self.order_engine.submit_exit(exit_signal, position)

        candidates = []
        scored_count = 0
        qualified_count = 0
        for symbol in self.universe_builder.symbols:
            bars = self.market_data_collector.get_recent_bars(symbol, 30)
            if len(bars) < 20:
                continue
            scored_count += 1
            score = self.strategy_scorer.score(bars)
            if score.score_total >= 70:
                qualified_count += 1
                self._save_strategy_snapshot(score)
            candidates.append(score)
        self._record_market_scan_summary(
            universe_count=len(self.universe_builder.symbols),
            scored_count=scored_count,
            qualified_count=qualified_count,
            top_candidate_count=min(len(candidates), 10),
        )
        self._send_top_candidate_scores(candidates)
        portfolio = self.portfolio_service.snapshot()
        for signal in self.signal_engine.evaluate_entry(candidates):
            decision = self.risk_engine.can_enter(signal, portfolio)
            if not decision.allowed:
                self._record_entry_skipped(signal, decision)
                continue
            sizing = self.risk_engine.target_order_size(signal, portfolio)
            self.order_engine.submit_entry(signal, sizing)

    def run_post_market(self) -> None:
        if not self.trading_calendar.is_trading_day(datetime.now()):
            return
        self.recovery_service.recover()
        self._send_daily_report()

    def _run_pre_market_once(self, now: datetime) -> None:
        current_day = now.strftime("%Y-%m-%d")
        if self._last_pre_market_run_date == current_day:
            return
        self.run_pre_market()
        self._last_pre_market_run_date = current_day
        self._last_post_market_run_date = None

    def _run_post_market_once(self, now: datetime) -> None:
        current_day = now.strftime("%Y-%m-%d")
        if self._last_post_market_run_date == current_day:
            return
        self.run_post_market()
        self._last_post_market_run_date = current_day

    def _run_market_cycle(self, now: datetime) -> None:
        if self._last_market_scan_at is None:
            self.run_market_scan()
            self._last_market_scan_at = now
            return
        elapsed = (now - self._last_market_scan_at).total_seconds()
        if elapsed >= self.market_scan_interval_seconds:
            self.run_market_scan()
            self._last_market_scan_at = now

    @staticmethod
    def _build_quote_subscription_symbols(symbols: list[str], portfolio: object | None) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            selected.append(symbol)
        if portfolio is None:
            return selected
        for position in getattr(portfolio, 'open_positions', []):
            symbol = getattr(position, 'symbol', '')
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            selected.append(symbol)
        return selected

    def _refresh_market_data(self, symbols: list[str]) -> None:
        if self.market_data_refresher is None:
            return
        try:
            self.market_data_refresher(symbols)
        except Exception as exc:
            self._record_system_event(
                event_type='market_data_refresh_failed',
                severity='ERROR',
                component='scheduler',
                message='Failed to refresh market data from REST.',
                payload={'error': str(exc)},
            )

    def _refresh_universe_master(self) -> None:
        if self.universe_master_refresher is None:
            return
        try:
            self.universe_master_refresher()
        except Exception:
            return

    def _refresh_holiday_calendar(self) -> None:
        if self.holiday_calendar_refresher is None:
            return
        try:
            self.holiday_calendar_refresher()
            self.trading_calendar.load()
        except Exception:
            return

    def _save_strategy_snapshot(self, score: object) -> None:
        if self.strategy_snapshots_repository is None:
            return
        try:
            self.strategy_snapshots_repository.create(score)
        except Exception:
            return

    def _rebuild_or_restore_market_universe(self) -> list[object]:
        try:
            items = self.universe_builder.rebuild(datetime.now())
        except Exception as exc:
            self._record_system_event(
                event_type="market_universe_rebuild_failed",
                severity="ERROR",
                component="scheduler",
                message="Failed to rebuild market universe.",
                payload={"error": str(exc)},
            )
            return self._load_cached_market_universe(log_if_empty=True)

        if items:
            return items

        self._record_system_event(
            event_type="market_universe_rebuild_empty",
            severity="WARN",
            component="scheduler",
            message="Market universe rebuild returned no symbols. Falling back to cached universe.",
            payload={},
        )
        return self._load_cached_market_universe(log_if_empty=True)

    def _load_cached_market_universe(self, *, log_if_empty: bool = False) -> list[object]:
        items = self.universe_builder.load_current_universe()
        if items or not log_if_empty:
            return items
        self._record_system_event(
            event_type="market_universe_cache_empty",
            severity="WARN",
            component="scheduler",
            message="Cached market universe is empty.",
            payload={},
        )
        return items

    def _ensure_market_universe_ready(self) -> None:
        if self.universe_builder.symbols:
            return
        try:
            items = self._load_cached_market_universe()
            if not items:
                items = self._rebuild_or_restore_market_universe()
        except Exception as exc:
            self._record_system_event(
                event_type="market_universe_restore_failed",
                severity="ERROR",
                component="scheduler",
                message="Failed to restore market universe.",
                payload={"error": str(exc)},
            )
            return

    def _build_position_exit_snapshot(self, position: object) -> MarketSnapshot | None:
        latest_snapshot = self.market_data_collector.get_latest_snapshot(position.symbol)
        bars = self.market_data_collector.get_recent_bars(position.symbol, 30)

        if len(bars) >= 20:
            score = self.strategy_scorer.score(bars)
            return MarketSnapshot(
                symbol=position.symbol,
                price=score.price,
                ma5=score.ma5,
                ma20=score.ma20,
                rsi=score.rsi,
                atr=score.atr,
                momentum_20=score.momentum_20,
                volume_ratio=score.volume_ratio,
            )

        if latest_snapshot is not None and latest_snapshot.price > 0:
            return MarketSnapshot(
                symbol=position.symbol,
                price=latest_snapshot.price,
                volume=latest_snapshot.volume,
                turnover=latest_snapshot.turnover,
            )

        current_price = float(getattr(position, 'current_price', 0.0) or 0.0)
        if current_price > 0:
            return MarketSnapshot(symbol=position.symbol, price=current_price)
        return None

    def _send_daily_report(self) -> None:
        if self.notifier is None or self.daily_report_builder is None:
            return
        try:
            payload = self.daily_report_builder()
        except Exception as exc:
            self._record_system_event(
                event_type="daily_report_build_failed",
                severity="ERROR",
                component="scheduler",
                message="Failed to build daily report.",
                payload={"error": str(exc)},
            )
            return
        if not isinstance(payload, dict) or not payload.get('message'):
            return
        try:
            self.notifier.send_daily_report(payload)
        except Exception as exc:
            self._record_system_event(
                event_type="daily_report_send_failed",
                severity="ERROR",
                component="scheduler",
                message="Failed to send daily report.",
                payload={"error": str(exc)},
            )

    def _record_entry_skipped(self, signal: object, decision: object) -> None:
        self._record_system_event(
            event_type='entry_skipped',
            severity='INFO',
            component='scheduler',
            message='Entry signal skipped due to risk decision.',
            payload={
                'symbol': getattr(signal, 'symbol', ''),
                'reason': getattr(decision, 'reason', ''),
                'score_total': getattr(signal, 'score_total', None),
                'price': getattr(signal, 'price', None),
            },
        )

    def _record_market_scan_summary(
        self,
        *,
        universe_count: int,
        scored_count: int,
        qualified_count: int,
        top_candidate_count: int,
    ) -> None:
        if self.system_events_repository is None:
            return
        try:
            self.system_events_repository.create(
                event_type="market_scan_summary",
                severity="INFO",
                component="scheduler",
                message="Recorded latest market scan summary.",
                payload={
                    "universe_count": universe_count,
                    "scored_count": scored_count,
                    "qualified_count": qualified_count,
                    "top_candidate_count": top_candidate_count,
                    "snapshot_time": datetime.now().isoformat(),
                },
            )
        except Exception:
            return

    def _record_system_event(
        self,
        *,
        event_type: str,
        severity: str,
        component: str,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        if self.system_events_repository is None:
            return
        try:
            self.system_events_repository.create(
                event_type=event_type,
                severity=severity,
                component=component,
                message=message,
                payload=payload,
            )
        except Exception:
            return

    def _send_top_candidate_scores(self, candidates: list[object]) -> None:
        if self.notifier is None or not candidates:
            return
        ranked = sorted(candidates, key=lambda item: item.score_total, reverse=True)[:10]
        signature = tuple((item.symbol, int(item.score_total), float(item.price)) for item in ranked)
        if signature == self._last_target_scores_signature:
            return
        try:
            self.notifier.send_target_scores(
                {
                    "snapshot_time": datetime.now().isoformat(),
                    "items": [
                        {
                            "symbol": item.symbol,
                            "score_total": item.score_total,
                            "price": item.price,
                        }
                        for item in ranked
                    ],
                }
            )
            self._last_target_scores_signature = signature
        except Exception:
            return

    @staticmethod
    def _is_pre_market(now: datetime) -> bool:
        return time(8, 45) <= now.time() < time(9, 0)

    @staticmethod
    def _is_market_open(now: datetime) -> bool:
        return time(9, 0) <= now.time() < time(15, 20)

    @staticmethod
    def _is_post_market(now: datetime) -> bool:
        return time(15, 20) <= now.time() < time(16, 0)
