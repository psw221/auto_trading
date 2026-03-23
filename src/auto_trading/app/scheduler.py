from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
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
    market_data_stale_after_seconds: int = 120
    universe_refresh_interval_seconds: int = 90
    entry_pause_after_position_mismatch_seconds: int = 180
    exit_retry_cooldown_seconds: int = 180
    loop_sleep_seconds: float = 1.0
    _last_pre_market_run_date: str | None = field(init=False, default=None)
    _last_post_market_run_date: str | None = field(init=False, default=None)
    _last_market_scan_at: datetime | None = field(init=False, default=None)
    _last_target_scores_signature: tuple[tuple[str, int, float], ...] = field(init=False, default_factory=tuple)
    _last_market_data_alert_signature: tuple[tuple[str, ...], tuple[str, ...]] = field(init=False, default_factory=tuple)
    _last_market_data_alert_at: datetime | None = field(init=False, default=None)

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
            self._build_market_data_refresh_request([item.symbol for item in items], portfolio)
        )

    def run_market_scan(self) -> None:
        if not self.trading_calendar.is_trading_day(datetime.now()):
            return
        self._reconcile_orders_from_broker()
        self._sync_portfolio_from_broker()
        if self.fail_safe_monitor.should_block_new_orders():
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
        refresh_request = self._build_market_data_refresh_request(self.universe_builder.symbols, portfolio)
        self._refresh_market_data(refresh_request)
        for position in portfolio.open_positions:
            snapshot = self._build_position_exit_snapshot(position)
            if snapshot is None:
                continue
            exit_signal = self.signal_engine.evaluate_exit(position, snapshot)
            if exit_signal is None:
                continue
            decision = self.risk_engine.can_exit(exit_signal, portfolio)
            if decision.allowed:
                if self._should_cooldown_exit(position):
                    self._record_system_event(
                        event_type='exit_retry_cooled_down',
                        severity='INFO',
                        component='scheduler',
                        message='Skipped exit retry because a recent sell order was rejected.',
                        payload={
                            'symbol': getattr(position, 'symbol', ''),
                            'reason': getattr(exit_signal, 'reason', ''),
                        },
                    )
                    continue
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
        held_symbols = {getattr(position, 'symbol', '') for position in getattr(portfolio, 'open_positions', []) if getattr(position, 'symbol', '')}
        self._send_top_candidate_scores(candidates, excluded_symbols=held_symbols)
        portfolio = self.portfolio_service.snapshot()
        if self._should_pause_entries_due_to_position_sync():
            for signal in self.signal_engine.evaluate_entry(candidates):
                decision = type('Decision', (), {'allowed': False, 'reason': 'position_sync_unstable'})()
                self._record_entry_skipped(signal, decision)
            return
        for signal in self.signal_engine.evaluate_entry(candidates):
            decision = self.risk_engine.can_enter(signal, portfolio)
            if not decision.allowed:
                self._record_entry_skipped(signal, decision)
                continue
            sizing = self.risk_engine.target_order_size(signal, portfolio)
            try:
                self.order_engine.submit_entry(signal, sizing)
            except Exception as exc:
                self._record_system_event(
                    event_type='entry_submit_failed',
                    severity='ERROR',
                    component='scheduler',
                    message='Failed to submit entry order.',
                    payload={
                        'symbol': getattr(signal, 'symbol', ''),
                        'reason': str(exc),
                    },
                )
                continue

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
    def _build_market_data_refresh_request(symbols: list[str], portfolio: object | None) -> dict[str, object]:
        priority_symbols: list[str] = []
        priority_seen: set[str] = set()
        if portfolio is not None:
            for position in getattr(portfolio, 'open_positions', []):
                symbol = getattr(position, 'symbol', '')
                if not symbol or symbol in priority_seen:
                    continue
                priority_seen.add(symbol)
                priority_symbols.append(symbol)
        scan_symbols: list[str] = []
        scan_seen: set[str] = set(priority_seen)
        for symbol in symbols:
            if not symbol or symbol in scan_seen:
                continue
            scan_seen.add(symbol)
            scan_symbols.append(symbol)
        requested_symbols = priority_symbols + scan_symbols
        return {
            'priority_symbols': priority_symbols,
            'scan_symbols': scan_symbols,
            'requested_symbols': requested_symbols,
            'universe_refresh_interval_seconds': None,
        }

    def _refresh_market_data(self, request: dict[str, object]) -> None:
        if self.market_data_refresher is None:
            return
        payload = dict(request)
        payload['universe_refresh_interval_seconds'] = self.universe_refresh_interval_seconds
        try:
            result = self.market_data_refresher(payload)
        except Exception as exc:
            self._record_system_event(
                event_type='market_data_refresh_failed',
                severity='ERROR',
                component='scheduler',
                message='Failed to refresh market data from REST.',
                payload={'error': str(exc)},
            )
            return
        self._record_market_data_refresh_summary(payload, result if isinstance(result, dict) else None)

    def _reconcile_orders_from_broker(self) -> None:
        reconcile = getattr(self.order_engine, 'reconcile_unknown_orders', None)
        if not callable(reconcile):
            return
        try:
            reconcile()
        except Exception as exc:
            self._record_system_event(
                event_type='order_reconcile_failed',
                severity='ERROR',
                component='scheduler',
                message='Failed to reconcile broker order state.',
                payload={'error': str(exc)},
            )

    def _sync_portfolio_from_broker(self) -> None:
        sync = getattr(self.portfolio_service, 'sync_from_broker', None)
        if not callable(sync):
            return
        try:
            sync()
        except Exception as exc:
            self._record_system_event(
                event_type='portfolio_sync_failed',
                severity='ERROR',
                component='scheduler',
                message='Failed to sync broker positions.',
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
        refresh_status = self.market_data_collector.cache.get_refresh_status(position.symbol)
        is_stale = self._is_market_data_stale(refresh_status.last_success_at if refresh_status is not None else '')

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
                source='REST',
                refreshed_at=refresh_status.last_success_at if refresh_status is not None else '',
                indicators_ready=True,
                is_stale=is_stale,
            )

        if latest_snapshot is not None and latest_snapshot.price > 0:
            return MarketSnapshot(
                symbol=position.symbol,
                price=latest_snapshot.price,
                volume=latest_snapshot.volume,
                turnover=latest_snapshot.turnover,
                source=getattr(latest_snapshot, 'source', '') or (refresh_status.source if refresh_status is not None else ''),
                refreshed_at=getattr(latest_snapshot, 'refreshed_at', '') or (refresh_status.last_success_at if refresh_status is not None else ''),
                indicators_ready=False,
                is_stale=is_stale,
            )

        current_price = float(getattr(position, 'current_price', 0.0) or 0.0)
        if current_price > 0:
            return MarketSnapshot(
                symbol=position.symbol,
                price=current_price,
                source='POSITION',
                indicators_ready=False,
                is_stale=True,
            )
        return None

    def _is_market_data_stale(self, refreshed_at: str) -> bool:
        if not refreshed_at:
            return True
        try:
            refreshed_dt = datetime.fromisoformat(refreshed_at)
        except ValueError:
            return True
        if refreshed_dt.tzinfo is None:
            refreshed_dt = refreshed_dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - refreshed_dt).total_seconds() > self.market_data_stale_after_seconds

    def _should_pause_entries_due_to_position_sync(self) -> bool:
        if self.system_events_repository is None:
            return False
        exists_recent = getattr(self.system_events_repository, 'exists_recent_event', None)
        if not callable(exists_recent):
            return False
        try:
            return bool(
                exists_recent(
                    'position_mismatch',
                    within_seconds=self.entry_pause_after_position_mismatch_seconds,
                )
            )
        except Exception:
            return False

    def _should_cooldown_exit(self, position: object) -> bool:
        orders_repository = getattr(self.order_engine, 'orders_repository', None)
        if orders_repository is None:
            return False
        symbol = getattr(position, 'symbol', '')
        checker = getattr(orders_repository, 'has_recent_rejected_exit', None)
        open_finder = getattr(orders_repository, 'find_open_for_symbol', None)
        try:
            if callable(open_finder):
                open_orders = [item for item in open_finder(symbol) if getattr(item, 'side', '') == 'SELL']
                if open_orders:
                    return True
            if callable(checker):
                return bool(checker(symbol, within_seconds=self.exit_retry_cooldown_seconds))
        except Exception:
            return False
        return False

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
        report_date = str(payload.get('report_date', '')).strip()
        if self._daily_report_already_sent(report_date):
            self._record_system_event(
                event_type="daily_report_duplicate_skipped",
                severity="INFO",
                component="scheduler",
                message="Skipped duplicate daily report send.",
                payload={"report_date": report_date},
            )
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

    def _daily_report_already_sent(self, report_date: str) -> bool:
        if not report_date or self.system_events_repository is None:
            return False
        exists = getattr(self.system_events_repository, "exists_for_report_date", None)
        if not callable(exists):
            return False
        try:
            return bool(exists("daily_report_notification_sent", report_date))
        except Exception:
            return False

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

    def _record_market_data_refresh_summary(self, request: dict[str, object], refresh_result: dict[str, object] | None = None) -> None:
        if self.system_events_repository is None:
            return
        try:
            requested_symbols = list(request.get('requested_symbols') or [])
            summary = self.market_data_collector.build_refresh_summary(
                requested_symbols,
                stale_after_seconds=self.market_data_stale_after_seconds,
            )
            if refresh_result:
                summary.update(refresh_result)
            self.system_events_repository.create(
                event_type='market_data_refresh_summary',
                severity='INFO',
                component='scheduler',
                message='Recorded latest market data refresh summary.',
                payload=summary,
            )
            self._maybe_alert_market_data_degraded(summary)
        except Exception:
            return

    def _maybe_alert_market_data_degraded(self, summary: dict[str, object]) -> None:
        if self.notifier is None:
            return
        failed_symbols = tuple(str(item) for item in (summary.get('failed_symbols') or []) if str(item))
        stale_symbols = tuple(str(item) for item in (summary.get('stale_symbols') or []) if str(item))
        if not failed_symbols and not stale_symbols:
            self._last_market_data_alert_signature = tuple()
            self._last_market_data_alert_at = None
            return
        signature = (failed_symbols, stale_symbols)
        now = datetime.now()
        if (
            self._last_market_data_alert_at is not None
            and self._last_market_data_alert_signature == signature
            and (now - self._last_market_data_alert_at).total_seconds() < 300
        ):
            return
        failed_count = int(summary.get('failed_count') or 0)
        stale_count = int(summary.get('stale_symbol_count') or 0)
        parts: list[str] = []
        if failed_count:
            parts.append(f'REST refresh failed={failed_count}')
        if stale_count:
            parts.append(f'stale={stale_count}')
        if failed_symbols:
            parts.append('failed_symbols=' + ','.join(failed_symbols))
        if stale_symbols:
            parts.append('stale_symbols=' + ','.join(stale_symbols))
        message = ' / '.join(parts) or 'REST market-data refresh degraded.'
        self._record_system_event(
            event_type='market_data_refresh_degraded',
            severity='WARN',
            component='scheduler',
            message=message,
            payload=summary,
        )
        try:
            self.notifier.send_system_event(
                {
                    'severity': 'WARN',
                    'component': 'market_data',
                    'message': message,
                }
            )
            self._last_market_data_alert_signature = signature
            self._last_market_data_alert_at = now
        except Exception:
            return

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

    def _send_top_candidate_scores(self, candidates: list[object], *, excluded_symbols: set[str] | None = None) -> None:
        if self.notifier is None or not candidates:
            return
        excluded = {symbol for symbol in (excluded_symbols or set()) if symbol}
        eligible = [item for item in candidates if getattr(item, 'symbol', '') not in excluded]
        if not eligible:
            self._last_target_scores_signature = tuple()
            return
        ranked = sorted(eligible, key=lambda item: item.score_total, reverse=True)[:10]
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
