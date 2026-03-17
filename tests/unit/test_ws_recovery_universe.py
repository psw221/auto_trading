from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_trading.broker.dto import BrokerBalance, BrokerFillSnapshot, BrokerOrderSnapshot, BrokerPositionSnapshot
from auto_trading.broker.kis_ws_client import KISWebSocketClient
from auto_trading.config.schema import Settings
from auto_trading.failsafe.monitor import FailSafeMonitor
from auto_trading.failsafe.recovery import RecoveryService
from auto_trading.portfolio.models import Position
from auto_trading.portfolio.service import PortfolioService
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.fills import FillsRepository
from auto_trading.storage.repositories.orders import OrdersRepository
from auto_trading.storage.repositories.positions import PositionsRepository
from auto_trading.storage.repositories.system_events import SystemEventsRepository
from auto_trading.storage.repositories.trade_logs import TradeLogsRepository
from auto_trading.universe.builder import UniverseBuilder


FIXTURES = Path("tests/fixtures")


def build_settings(master_path: Path | None = None) -> Settings:
    return Settings(
        env="demo",
        db_path=Path("data/test_fixture.db"),
        kis_base_url="https://example.com",
        kis_ws_url="ws://example.com",
        kis_app_key="key",
        kis_app_secret="secret",
        kis_cano="123",
        kis_acnt_prdt_cd="01",
        kis_access_token="token",
        kis_refresh_token="",
        kis_user_id="user1",
        universe_master_path=master_path or Path("data/universe_master.csv"),
        holiday_calendar_path=Path("data/krx_holidays.csv"),
        holiday_api_service_key="",
        telegram_bot_token="",
        telegram_chat_id="",
    )


class KISClientStub:
    def __init__(self) -> None:
        self.settings = build_settings(FIXTURES / "universe_master_fixture.csv")

    def get_approval_key(self) -> str:
        return "approval"

    def get_positions(self):
        return [BrokerPositionSnapshot(symbol="005930", qty=2, avg_price=70000.0, current_price=71000.0, name="Samsung")]

    def get_open_orders(self):
        return []

    def get_daily_fills(self):
        return []

    def get_balance(self):
        return BrokerBalance(cash=1000000.0, total_asset=1200000.0)

    def get_current_price(self, symbol: str):
        return {
            "005930": {"price": 70000.0, "turnover": 10000000000.0},
            "069500": {"price": 35000.0, "turnover": 7000000000.0},
            "000001": {"price": 4000.0, "turnover": 1000000000.0},
            "000002": {"price": 2000.0, "turnover": 7000000000.0},
        }[symbol]

    def get_daily_turnover_history(self, symbol: str, lookback_days: int = 20):
        return {
            "005930": [{"turnover": 10000000000.0}] * 20,
            "069500": [{"turnover": 7000000000.0}] * 20,
            "000001": [{"turnover": 1000000000.0}] * 20,
            "000002": [{"turnover": 7000000000.0}] * 20,
        }[symbol]


class OrderEngineStub:
    def __init__(self, orders_repository: OrdersRepository) -> None:
        self.orders_repository = orders_repository

    def reconcile_unknown_orders(self) -> None:
        for order in self.orders_repository.find_unknown_orders():
            self.orders_repository.update_status(order.id, "ACKNOWLEDGED", remaining_qty=order.qty)


class FixtureBasedTests(unittest.TestCase):
    def test_universe_builder_keeps_existing_current_universe_when_rebuild_result_is_empty(self) -> None:
        current_path = FIXTURES / 'current_universe.csv'
        original = current_path.read_text(encoding='utf-8') if current_path.exists() else None
        current_path.write_text(
            'symbol,name,market,asset_type,price,avg_turnover_20d,kospi200\n'
            '005930,Samsung Electronics,KOSPI,STOCK,70000,10000000000,Y\n',
            encoding='utf-8',
        )

        client = KISClientStub()
        client.settings = build_settings(FIXTURES / 'universe_master_fixture.csv')
        client.settings.universe_master_path = FIXTURES / 'universe_master_fixture.csv'

        class _EmptyUniverseKISClient(KISClientStub):
            def __init__(self) -> None:
                self.settings = client.settings

            def get_current_price(self, symbol: str):
                return {
                    '005930': {'price': 1000.0, 'turnover': 10000000000.0},
                    '069500': {'price': 35000.0, 'turnover': 7000000000.0},
                    '000001': {'price': 4000.0, 'turnover': 1000000000.0},
                    '000002': {'price': 2000.0, 'turnover': 7000000000.0},
                }[symbol]

        try:
            builder = UniverseBuilder(_EmptyUniverseKISClient())
            items = builder.rebuild(__import__('datetime').datetime.now())
            self.assertEqual([], items)
            cached = current_path.read_text(encoding='utf-8')
            self.assertIn('005930,Samsung Electronics', cached)
        finally:
            if original is None:
                current_path.unlink(missing_ok=True)
            else:
                current_path.write_text(original, encoding='utf-8')


    def test_portfolio_sync_compacts_duplicate_local_positions(self) -> None:
        db_path = Path("data/test_fixture_duplicate_positions.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            KISClientStub(),
            system_events,
        )
        first = Position(symbol="005930", qty=1, status="ERROR")
        second = Position(symbol="005930", qty=2, status="OPEN")
        positions.upsert(first)
        positions.upsert(second)

        portfolio.sync_from_broker()
        portfolio.sync_from_broker()

        rows = positions.find_all_by_symbol("005930")
        active_rows = [row for row in rows if row.status in {"OPENING", "OPEN", "CLOSING"}]
        self.assertEqual(1, len(active_rows))
        self.assertEqual(2, active_rows[0].qty)
        compacted = [row for row in rows if row.id != active_rows[0].id][0]
        self.assertEqual("CLOSED", compacted.status)
        self.assertEqual(0, compacted.qty)
        self.assertEqual("duplicate_local_position", compacted.exit_reason)
        with db.transaction() as connection:
            event = connection.execute(
                "SELECT event_type, payload_json FROM system_events WHERE event_type = 'duplicate_local_position' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(event)

    def test_websocket_client_parses_order_notice_fixture(self) -> None:
        client = KISWebSocketClient(build_settings(), KISClientStub())
        payload = json.loads((FIXTURES / "kis_order_notice.json").read_text(encoding="utf-8"))
        client.feed_mock_message(payload)
        event = client.poll_events()[0]
        self.assertEqual("fill", event.event_type)
        self.assertEqual("005930", event.symbol)
        self.assertEqual("12345", event.payload["order_no"])

    def test_websocket_client_maps_order_notice_ack_from_numeric_flags(self) -> None:
        client = KISWebSocketClient(build_settings(), KISClientStub())
        client.register_aes_context("H0STCNI9", "mxjoquthlswzljlewsdkxafclghixfkl", "3f35902c77e60dcf")
        payload = (
            "1|H0STCNI9|001|"
            "LCjV+ft9Y1yviveiambdPZdpDGiN3SR2kYhR20B1T9CPqj72LVi+Lq5TxowCtg9oOIljgZCalvZvesLXESMTdqaPCDGTgH8GIo4iTSIy0wdW0FqBzKozoFnMSTbxaEqIW1qSkUe1T8pxoKS053lQCTVegK/9qPHRS4+5hS/fijF8MD5Cy49nQXjzIFnUY9ds"
        )
        client.feed_mock_message(payload)
        event = client.poll_events()[0]
        self.assertEqual("order", event.event_type)
        self.assertEqual("005930", event.symbol)
        self.assertEqual("ACKNOWLEDGED", event.payload["status"])
        self.assertEqual("0000030071", event.payload["order_no"])

    def test_websocket_client_parses_quote_tick_fixture(self) -> None:
        client = KISWebSocketClient(build_settings(), KISClientStub())
        payload = (FIXTURES / "kis_quote_tick.txt").read_text(encoding="utf-8").strip()
        client.feed_mock_message(payload)
        event = client.poll_events()[0]
        self.assertEqual("quote", event.event_type)
        self.assertEqual("71000", event.payload["price"])

    def test_recovery_service_clears_error_position_from_fixture_state(self) -> None:
        db_path = Path("data/test_fixture_recovery.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            KISClientStub(),
            system_events,
        )
        bad_position = Position(symbol="005930", qty=2, status="ERROR")
        positions.upsert(bad_position)
        recovery = RecoveryService(
            portfolio_service=portfolio,
            orders_repository=orders,
            positions_repository=positions,
            system_events_repository=system_events,
            order_engine=OrderEngineStub(orders),
            fail_safe_monitor=FailSafeMonitor(blocked=True, fallback_active=True),
        )
        recovery.recover()
        restored = positions.find_by_symbol("005930")
        self.assertIsNotNone(restored)
        self.assertEqual("OPEN", restored.status)

    def test_universe_builder_filters_fixture_rows_by_prd(self) -> None:
        builder = UniverseBuilder(KISClientStub())
        items = builder.rebuild(__import__("datetime").datetime.now())
        self.assertEqual(["005930"], [item.symbol for item in items])


if __name__ == "__main__":
    unittest.main()
