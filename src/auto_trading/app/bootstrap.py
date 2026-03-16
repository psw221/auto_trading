from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from auto_trading.app.dashboard import build_daily_report_summary, format_daily_report_summary
from auto_trading.app.scheduler import SchedulerService
from auto_trading.app.runtime import RuntimeService
from auto_trading.broker.kis_client import KISClient
from auto_trading.broker.kis_ws_client import KISWebSocketClient
from auto_trading.common.holiday_generator import generate_holiday_csv, needs_holiday_refresh
from auto_trading.common.trading_calendar import TradingCalendar
from auto_trading.config.schema import Settings
from auto_trading.config.settings import load_settings
from auto_trading.failsafe.monitor import FailSafeMonitor
from auto_trading.failsafe.recovery import RecoveryService
from auto_trading.market_data.cache import MarketDataCache
from auto_trading.market_data.collector import MarketDataCollector
from auto_trading.notifications.telegram import TelegramNotifier
from auto_trading.orders.engine import OrderEngine
from auto_trading.portfolio.service import PortfolioService
from auto_trading.risk.engine import RiskEngine
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.fills import FillsRepository
from auto_trading.storage.repositories.orders import OrdersRepository
from auto_trading.storage.repositories.positions import PositionsRepository
from auto_trading.storage.repositories.system_events import SystemEventsRepository
from auto_trading.storage.repositories.strategy_snapshots import StrategySnapshotsRepository
from auto_trading.storage.repositories.trade_logs import TradeLogsRepository
from auto_trading.strategy.scorer import StrategyScorer
from auto_trading.strategy.signals import SignalEngine
from auto_trading.universe.builder import UniverseBuilder
from auto_trading.universe.master_generator import generate_master_csv


@dataclass(slots=True)
class ApplicationContainer:
    settings: Settings
    db: Database
    kis_client: KISClient
    kis_ws_client: KISWebSocketClient
    market_data_collector: MarketDataCollector
    universe_builder: UniverseBuilder
    strategy_scorer: StrategyScorer
    signal_engine: SignalEngine
    portfolio_service: PortfolioService
    risk_engine: RiskEngine
    order_engine: OrderEngine
    fail_safe_monitor: FailSafeMonitor
    recovery_service: RecoveryService
    notifier: TelegramNotifier
    scheduler: SchedulerService
    runtime: RuntimeService


def bootstrap() -> ApplicationContainer:
    settings = load_settings()
    db = Database(settings.db_path)
    db.initialize()
    orders_repository = OrdersRepository(db)
    positions_repository = PositionsRepository(db)
    fills_repository = FillsRepository(db)
    trade_logs_repository = TradeLogsRepository(db)
    system_events_repository = SystemEventsRepository(db)
    strategy_snapshots_repository = StrategySnapshotsRepository(db)
    kis_client = KISClient(settings)
    kis_ws_client = KISWebSocketClient(settings, kis_client)
    market_cache = MarketDataCache()
    market_data_collector = MarketDataCollector(market_cache)
    universe_builder = UniverseBuilder(kis_client)
    strategy_scorer = StrategyScorer()
    signal_engine = SignalEngine()
    portfolio_service = PortfolioService(
        positions_repository,
        orders_repository,
        fills_repository,
        trade_logs_repository,
        kis_client,
        system_events_repository,
    )
    risk_engine = RiskEngine(settings)
    notifier = TelegramNotifier(settings, system_events_repository)
    fail_safe_monitor = FailSafeMonitor()
    trading_calendar = TradingCalendar(settings.holiday_calendar_path)
    recovery_service = RecoveryService(
        portfolio_service=portfolio_service,
        orders_repository=orders_repository,
        positions_repository=positions_repository,
        system_events_repository=system_events_repository,
        order_engine=None,
        fail_safe_monitor=fail_safe_monitor,
    )
    order_engine = OrderEngine(
        kis_client=kis_client,
        orders_repository=orders_repository,
        positions_repository=positions_repository,
        portfolio_service=portfolio_service,
        system_events_repository=system_events_repository,
        notifier=notifier,
        fail_safe_monitor=fail_safe_monitor,
    )
    recovery_service.order_engine = order_engine
    scheduler = SchedulerService(
        universe_builder=universe_builder,
        market_data_collector=market_data_collector,
        strategy_scorer=strategy_scorer,
        signal_engine=signal_engine,
        portfolio_service=portfolio_service,
        risk_engine=risk_engine,
        order_engine=order_engine,
        recovery_service=recovery_service,
        fail_safe_monitor=fail_safe_monitor,
        trading_calendar=trading_calendar,
        notifier=notifier,
        system_events_repository=system_events_repository,
        strategy_snapshots_repository=strategy_snapshots_repository,
        quote_subscription_updater=kis_ws_client.subscribe_quotes,
        universe_master_refresher=lambda: generate_master_csv(output=settings.universe_master_path),
        holiday_calendar_refresher=lambda: _refresh_holiday_calendar(settings),
        daily_report_builder=lambda: {
            'message': format_daily_report_summary(
                build_daily_report_summary(
                    settings.db_path,
                    settings.universe_master_path,
                )
            )
        },
    )
    runtime = RuntimeService(
        kis_ws_client=kis_ws_client,
        market_data_collector=market_data_collector,
        order_engine=order_engine,
        fail_safe_monitor=fail_safe_monitor,
    )
    return ApplicationContainer(
        settings=settings,
        db=db,
        kis_client=kis_client,
        kis_ws_client=kis_ws_client,
        market_data_collector=market_data_collector,
        universe_builder=universe_builder,
        strategy_scorer=strategy_scorer,
        signal_engine=signal_engine,
        portfolio_service=portfolio_service,
        risk_engine=risk_engine,
        order_engine=order_engine,
        fail_safe_monitor=fail_safe_monitor,
        recovery_service=recovery_service,
        notifier=notifier,
        scheduler=scheduler,
        runtime=runtime,
    )


def _refresh_holiday_calendar(settings: Settings) -> None:
    current_year = datetime.now().year
    if not needs_holiday_refresh(settings.holiday_calendar_path, current_year):
        return
    generate_holiday_csv(
        output=settings.holiday_calendar_path,
        year=current_year,
        service_key=settings.holiday_api_service_key,
    )
