from __future__ import annotations

import unittest
from pathlib import Path

from auto_trading.broker.dto import BrokerBalance, BrokerFillSnapshot, BrokerOrderResponse, BrokerOrderSnapshot, BrokerRealtimeEvent
from auto_trading.common.exceptions import BrokerApiError
from auto_trading.config.schema import Settings
from auto_trading.failsafe.monitor import FailSafeMonitor
from auto_trading.notifications.telegram import TelegramNotifier
from auto_trading.orders.engine import OrderEngine
from auto_trading.portfolio.service import PortfolioService
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.fills import FillsRepository
from auto_trading.storage.repositories.orders import OrdersRepository
from auto_trading.storage.repositories.positions import PositionsRepository
from auto_trading.storage.repositories.system_events import SystemEventsRepository
from auto_trading.storage.repositories.trade_logs import TradeLogsRepository
from auto_trading.strategy.models import EntrySignal, ExitSignal, OrderSizing


class FailingBroker:
    def place_cash_order(self, request):
        raise BrokerApiError("network down")

    def get_balance(self):
        return BrokerBalance(cash=1000000.0, total_asset=1000000.0)


class SuccessBroker:
    def __init__(self) -> None:
        self.order_no = "ORDER-0001"
        self.last_request = None

    def place_cash_order(self, request):
        self.last_request = request
        return BrokerOrderResponse(
            order_no=self.order_no,
            accepted=True,
            rt_cd="0",
            msg_cd="0",
            msg="ok",
            output={"ODNO": self.order_no},
        )

    def get_balance(self):
        return BrokerBalance(cash=1000000.0, total_asset=1000000.0)

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def get_daily_fills(self):
        return []


class CapturingNotifier:
    def __init__(self) -> None:
        self.trade_fill_payloads: list[dict[str, object]] = []

    def send_trade_fill(self, payload: dict[str, object]) -> None:
        self.trade_fill_payloads.append(payload)



def build_settings() -> Settings:
    return Settings(
        env="demo",
        db_path=Path("data/test_engine.db"),
        kis_base_url="https://example.com",
        kis_ws_url="ws://example.com",
        kis_app_key="key",
        kis_app_secret="secret",
        kis_cano="123",
        kis_acnt_prdt_cd="01",
        kis_access_token="token",
        kis_refresh_token="",
        kis_user_id="user1",
        universe_master_path=Path("data/universe_master.csv"),
        holiday_calendar_path=Path("data/krx_holidays.csv"),
        holiday_api_service_key="",
        telegram_bot_token="",
        telegram_chat_id="",
    )


class OrderEngineExceptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data/test_engine_runtime.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.db = Database(self.db_path)
        self.db.initialize()
        self.orders = OrdersRepository(self.db)
        self.positions = PositionsRepository(self.db)
        self.system_events = SystemEventsRepository(self.db)
        portfolio = PortfolioService(
            self.positions,
            self.orders,
            FillsRepository(self.db),
            TradeLogsRepository(self.db),
            FailingBroker(),
            self.system_events,
        )
        self.monitor = FailSafeMonitor()
        self.engine = OrderEngine(
            kis_client=FailingBroker(),
            orders_repository=self.orders,
            positions_repository=self.positions,
            portfolio_service=portfolio,
            system_events_repository=self.system_events,
            notifier=TelegramNotifier(build_settings(), self.system_events),
            fail_safe_monitor=self.monitor,
        )

    def test_submit_entry_marks_unknown_and_blocks_on_broker_error(self) -> None:
        order = self.engine.submit_entry(
            EntrySignal(symbol="005930", score_total=80, price=70000.0),
            OrderSizing(qty=1, order_type="LIMIT", price=70000.0),
        )
        saved = self.orders.find_by_id(order.id)
        position = self.positions.find_by_symbol("005930")
        self.assertIsNotNone(saved)
        self.assertEqual("UNKNOWN", saved.status)
        self.assertIsNotNone(position)
        self.assertEqual("ERROR", position.status)
        self.assertTrue(self.monitor.should_block_new_orders())

    def test_handle_fill_keeps_zero_remaining_qty_after_full_fill(self) -> None:
        db_path = Path("data/test_engine_fill_runtime.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)
        broker = SuccessBroker()
        notifier = CapturingNotifier()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            broker,
            system_events,
        )
        engine = OrderEngine(
            kis_client=broker,
            orders_repository=orders,
            positions_repository=positions,
            portfolio_service=portfolio,
            system_events_repository=system_events,
            notifier=notifier,
            fail_safe_monitor=FailSafeMonitor(),
        )
        order = engine.submit_entry(
            EntrySignal(symbol="005930", score_total=80, price=70000.0),
            OrderSizing(qty=3, order_type="LIMIT", price=70000.0),
        )
        engine.handle_broker_event(
            BrokerRealtimeEvent(
                event_type="fill",
                symbol="005930",
                payload={
                    "order_no": "ORDER-0001",
                    "symbol": "005930",
                    "side": "BUY",
                    "fill_qty": "1",
                    "fill_price": "70000",
                    "filled_at": "2026-03-12T09:10:00+09:00",
                },
            )
        )
        engine.handle_broker_event(
            BrokerRealtimeEvent(
                event_type="fill",
                symbol="005930",
                payload={
                    "order_no": "ORDER-0001",
                    "symbol": "005930",
                    "side": "BUY",
                    "fill_qty": "2",
                    "fill_price": "70000",
                    "filled_at": "2026-03-12T09:10:05+09:00",
                },
            )
        )
        saved = orders.find_by_id(order.id)
        self.assertIsNotNone(saved)
        self.assertEqual("FILLED", saved.status)
        self.assertEqual(3, saved.filled_qty)
        self.assertEqual(0, saved.remaining_qty)
        self.assertEqual(2, len(notifier.trade_fill_payloads))
        last_payload = notifier.trade_fill_payloads[-1]
        self.assertEqual("ENTRY", last_payload["reason"])
        self.assertEqual(3, last_payload["filled_qty"])
        self.assertEqual(3, last_payload["total_qty"])
        self.assertEqual(0, last_payload["remaining_qty"])
        self.assertEqual(3, last_payload["position_qty"])

    def test_submit_entry_blocks_when_active_position_exists(self) -> None:
        self.positions.upsert(__import__('auto_trading.portfolio.models', fromlist=['Position']).Position(symbol='005930', qty=1, status='OPEN'))
        with self.assertRaises(RuntimeError):
            self.engine.submit_entry(
                EntrySignal(symbol="005930", score_total=80, price=70000.0),
                OrderSizing(qty=1, order_type="LIMIT", price=70000.0),
            )
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT event_type, message FROM system_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual("duplicate_position", row["event_type"])
        self.assertIn("active position already exists", row["message"].lower())

    def test_submit_exit_passes_limit_price_from_signal(self) -> None:
        db_path = Path("data/test_engine_exit_runtime.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)
        broker = SuccessBroker()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            broker,
            system_events,
        )
        engine = OrderEngine(
            kis_client=broker,
            orders_repository=orders,
            positions_repository=positions,
            portfolio_service=portfolio,
            system_events_repository=system_events,
            notifier=CapturingNotifier(),
            fail_safe_monitor=FailSafeMonitor(),
        )
        position = __import__('auto_trading.portfolio.models', fromlist=['Position']).Position(
            symbol='088350',
            qty=527,
            avg_entry_price=4735.0,
            current_price=4925.0,
            status='OPEN',
        )
        positions.upsert(position)

        order = engine.submit_exit(
            ExitSignal(symbol='088350', reason='take_profit', order_type='LIMIT', price=4925.0),
            position,
        )

        self.assertIsNotNone(broker.last_request)
        self.assertEqual('SELL', broker.last_request.side)
        self.assertEqual('LIMIT', broker.last_request.order_type)
        self.assertEqual(4925.0, broker.last_request.price)
        saved = orders.find_by_id(order.id)
        self.assertIsNotNone(saved)
        self.assertEqual(4925.0, saved.price)

    def test_reconcile_submitted_order_applies_fill_and_sends_notification(self) -> None:
        db_path = Path("data/test_engine_reconcile_submitted_runtime.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _ReconcilingBroker(SuccessBroker):
            def get_open_orders(self):
                return []

            def get_daily_fills(self):
                return [
                    BrokerFillSnapshot(
                        order_no='ORDER-SELL-1',
                        symbol='006360',
                        side='SELL',
                        fill_qty=10,
                        fill_price=30000.0,
                        filled_at='2026-03-20T09:35:55+09:00',
                    )
                ]

        broker = _ReconcilingBroker()
        notifier = CapturingNotifier()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            broker,
            system_events,
        )
        engine = OrderEngine(
            kis_client=broker,
            orders_repository=orders,
            positions_repository=positions,
            portfolio_service=portfolio,
            system_events_repository=system_events,
            notifier=notifier,
            fail_safe_monitor=FailSafeMonitor(),
        )
        position = __import__('auto_trading.portfolio.models', fromlist=['Position']).Position(
            symbol='006360',
            qty=10,
            avg_entry_price=26250.0,
            current_price=30000.0,
            status='OPEN',
        )
        positions.upsert(position)
        order = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='006360',
            side='SELL',
            qty=10,
            order_type='LIMIT',
            intent='TAKE_PROFIT',
            position_id=position.id,
            broker_order_id='ORDER-SELL-1',
            status='SUBMITTED',
            filled_qty=0,
            remaining_qty=10,
            price=30000.0,
        )
        orders.create(order)

        engine.reconcile_unknown_orders()

        saved = orders.find_by_id(order.id)
        self.assertIsNotNone(saved)
        self.assertEqual('FILLED', saved.status)
        self.assertEqual(10, saved.filled_qty)
        self.assertEqual(0, saved.remaining_qty)
        self.assertEqual(1, len(notifier.trade_fill_payloads))
        payload = notifier.trade_fill_payloads[0]
        self.assertEqual('006360', payload['symbol'])
        self.assertEqual('TAKE_PROFIT', payload['reason'])
        with db.transaction() as connection:
            row = connection.execute("SELECT COUNT(*) AS cnt FROM fills WHERE order_id = ?", (order.id,)).fetchone()
        self.assertEqual(1, row['cnt'])

    def test_reconcile_unknown_buy_order_recovers_from_broker_holdings(self) -> None:
        db_path = Path("data/test_engine_reconcile_unknown_buy_runtime.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _HoldingBroker(SuccessBroker):
            def get_positions(self):
                return [
                    __import__('auto_trading.broker.dto', fromlist=['BrokerPositionSnapshot']).BrokerPositionSnapshot(
                        symbol='100840', qty=51, avg_price=51200.0, current_price=52800.0, name='SNT에너지'
                    )
                ]

            def get_open_orders(self):
                return []

            def get_daily_fills(self):
                return []

        broker = _HoldingBroker()
        notifier = CapturingNotifier()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            broker,
            system_events,
        )
        engine = OrderEngine(
            kis_client=broker,
            orders_repository=orders,
            positions_repository=positions,
            portfolio_service=portfolio,
            system_events_repository=system_events,
            notifier=notifier,
            fail_safe_monitor=FailSafeMonitor(),
        )
        position = __import__('auto_trading.portfolio.models', fromlist=['Position']).Position(
            symbol='100840',
            qty=51,
            avg_entry_price=51200.0,
            current_price=52800.0,
            status='OPEN',
        )
        positions.upsert(position)
        order = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='100840',
            side='BUY',
            qty=51,
            order_type='LIMIT',
            intent='ENTRY',
            position_id=position.id,
            broker_order_id='ORDER-BUY-100840',
            status='UNKNOWN',
            filled_qty=0,
            remaining_qty=51,
            price=51200.0,
        )
        orders.create(order)

        engine.reconcile_unknown_orders()

        saved = orders.find_by_id(order.id)
        self.assertIsNotNone(saved)
        self.assertEqual('FILLED', saved.status)
        self.assertEqual(51, saved.filled_qty)
        self.assertEqual(0, saved.remaining_qty)
        with db.transaction() as connection:
            row = connection.execute(
                "SELECT event_type FROM system_events WHERE event_type = 'unknown_buy_order_recovered' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)



if __name__ == "__main__":
    unittest.main()
