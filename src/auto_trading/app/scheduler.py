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
    quote_subscription_updater: object | None = None
    universe_master_refresher: object | None = None
    holiday_calendar_refresher: object | None = None
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
        items = self.universe_builder.rebuild(datetime.now())
        self._update_quote_subscriptions([item.symbol for item in items])

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
        for position in portfolio.open_positions:
            bars = self.market_data_collector.get_recent_bars(position.symbol, 30)
            if len(bars) < 20:
                continue
            score = self.strategy_scorer.score(bars)
            snapshot = MarketSnapshot(
                symbol=position.symbol,
                price=score.price,
                ma5=score.ma5,
                ma20=score.ma20,
                rsi=score.rsi,
                atr=score.atr,
                momentum_20=score.momentum_20,
                volume_ratio=score.volume_ratio,
            )
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
            if decision.allowed:
                sizing = self.risk_engine.target_order_size(signal, portfolio)
                self.order_engine.submit_entry(signal, sizing)

    def run_post_market(self) -> None:
        if not self.trading_calendar.is_trading_day(datetime.now()):
            return
        self.recovery_service.recover()

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

    def _update_quote_subscriptions(self, symbols: list[str]) -> None:
        if self.quote_subscription_updater is None:
            return
        self.quote_subscription_updater(symbols)

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

    def _ensure_market_universe_ready(self) -> None:
        if self.universe_builder.symbols:
            return
        try:
            items = self.universe_builder.load_current_universe()
            if not items:
                items = self.universe_builder.rebuild(datetime.now())
        except Exception:
            return
        self._update_quote_subscriptions([item.symbol for item in items])

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
