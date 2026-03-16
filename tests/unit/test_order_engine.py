from __future__ import annotations

import unittest
from pathlib import Path

from auto_trading.broker.dto import BrokerBalance, BrokerOrderResponse, BrokerRealtimeEvent
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
from auto_trading.strategy.models import EntrySignal, OrderSizing


class FailingBroker:
    def place_cash_order(self, request):
        raise BrokerApiError("network down")

    def get_balance(self):
        return BrokerBalance(cash=1000000.0, total_asset=1000000.0)


class SuccessBroker:
    def __init__(self) -> None:
        self.order_no = "ORDER-0001"

    def place_cash_order(self, request):
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


if __name__ == "__main__":
    unittest.main()
