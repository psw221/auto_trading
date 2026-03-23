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


class CapturingNotifier:
    def __init__(self) -> None:
        self.system_event_payloads: list[dict[str, object]] = []
        self.trade_fill_payloads: list[dict[str, object]] = []
        self.trade_recovery_payloads: list[dict[str, object]] = []

    def send_system_event(self, payload: dict[str, object]) -> None:
        self.system_event_payloads.append(payload)

    def send_trade_fill(self, payload: dict[str, object]) -> None:
        self.trade_fill_payloads.append(payload)

    def send_trade_recovery(self, payload: dict[str, object]) -> None:
        self.trade_recovery_payloads.append(payload)


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

    def test_portfolio_sync_keeps_active_position_when_broker_temporarily_omits_symbol(self) -> None:
        db_path = Path("data/test_fixture_sync_keeps_active.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _MissingHoldingsClient(KISClientStub):
            def get_positions(self):
                return []

        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _MissingHoldingsClient(),
            system_events,
        )
        active = Position(symbol="005930", qty=2, status="OPEN")
        positions.upsert(active)

        portfolio.sync_from_broker()

        restored = positions.find_active_by_symbol("005930")
        self.assertIsNotNone(restored)
        self.assertEqual("OPEN", restored.status)
        self.assertEqual(2, restored.qty)
        with db.transaction() as connection:
            event = connection.execute(
                "SELECT event_type, message FROM system_events WHERE event_type = 'position_mismatch' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(event)

    def test_portfolio_sync_restores_closed_row_when_broker_still_has_symbol(self) -> None:
        db_path = Path("data/test_fixture_sync_restore_closed.db")
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
        closed = Position(symbol="005930", qty=0, status="CLOSED", closed_at="2026-03-19T00:00:00+00:00", exit_reason="broker_position_missing")
        positions.upsert(closed)

        portfolio.sync_from_broker()

        restored = positions.find_active_by_symbol("005930")
        self.assertIsNotNone(restored)
        self.assertEqual("OPEN", restored.status)
        self.assertEqual(2, restored.qty)
        self.assertEqual(70000.0, restored.avg_entry_price)
        with db.transaction() as connection:
            event = connection.execute(
                "SELECT event_type, payload_json FROM system_events WHERE event_type = 'position_recovered' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(event)

    def test_portfolio_sync_closes_missing_position_after_unresolved_sell(self) -> None:
        db_path = Path("data/test_fixture_sync_force_close.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _NoHoldingClient(KISClientStub):
            def get_positions(self):
                return []

            def get_open_orders(self):
                return []

            def get_daily_fills(self):
                return []

        notifier = CapturingNotifier()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _NoHoldingClient(),
            system_events,
            notifier,
        )
        active = Position(symbol="005930", qty=2, status="OPEN", current_price=71000.0)
        positions.upsert(active)
        order = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='005930',
            side='SELL',
            qty=2,
            order_type='LIMIT',
            intent='TAKE_PROFIT',
            position_id=active.id,
            broker_order_id='ORDER-SELL-UNKNOWN',
            status='UNKNOWN',
            filled_qty=0,
            remaining_qty=2,
            price=71000.0,
        )
        orders.create(order)

        portfolio.sync_from_broker()
        first_pass_position = positions.find_by_id(active.id)
        first_pass_order = orders.find_by_id(order.id)
        self.assertIsNotNone(first_pass_position)
        self.assertEqual("OPEN", first_pass_position.status)
        self.assertEqual(2, first_pass_position.qty)
        self.assertIsNotNone(first_pass_order)
        self.assertEqual("UNKNOWN", first_pass_order.status)
        self.assertIn("absence_check:1", first_pass_order.failure_reason or "")

        portfolio.sync_from_broker()

        restored = positions.find_by_id(active.id)
        self.assertIsNotNone(restored)
        self.assertEqual("CLOSED", restored.status)
        self.assertEqual(0, restored.qty)
        self.assertEqual("broker_position_absent_after_sell", restored.exit_reason)
        saved_order = orders.find_by_id(order.id)
        self.assertIsNotNone(saved_order)
        self.assertEqual("FILLED", saved_order.status)
        self.assertEqual(0, saved_order.remaining_qty)
        with db.transaction() as connection:
            check_event = connection.execute(
                "SELECT event_type FROM system_events WHERE event_type = 'broker_position_absent_after_unresolved_sell_check' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            close_event = connection.execute(
                "SELECT event_type FROM system_events WHERE event_type = 'position_closed_from_broker_absence' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(check_event)
        self.assertIsNotNone(close_event)
        self.assertEqual(1, len(notifier.trade_recovery_payloads))
        self.assertEqual('SELL', notifier.trade_recovery_payloads[0]['side'])
        self.assertEqual('005930', notifier.trade_recovery_payloads[0]['symbol'])

    def test_portfolio_sync_uses_latest_unresolved_sell_even_if_newer_sell_was_rejected(self) -> None:
        db_path = Path("data/test_fixture_sync_ignores_later_rejected_sell.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _NoHoldingClient(KISClientStub):
            def get_positions(self):
                return []

            def get_open_orders(self):
                return []

            def get_daily_fills(self):
                return []

        notifier = CapturingNotifier()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _NoHoldingClient(),
            system_events,
            notifier,
        )
        active = Position(symbol="005930", qty=2, status="OPEN", current_price=71000.0)
        positions.upsert(active)
        unresolved = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='005930',
            side='SELL',
            qty=2,
            order_type='LIMIT',
            intent='TAKE_PROFIT',
            position_id=active.id,
            broker_order_id='ORDER-SELL-UNKNOWN',
            status='UNKNOWN',
            filled_qty=0,
            remaining_qty=2,
            price=71000.0,
        )
        orders.create(unresolved)
        rejected = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='005930',
            side='SELL',
            qty=2,
            order_type='LIMIT',
            intent='MA5_BREAKDOWN',
            position_id=active.id,
            broker_order_id='',
            status='REJECTED',
            filled_qty=0,
            remaining_qty=2,
            price=70500.0,
            failure_reason='mock reject',
        )
        orders.create(rejected)

        portfolio.sync_from_broker()
        first_pass_order = orders.find_by_id(unresolved.id)
        self.assertIsNotNone(first_pass_order)
        self.assertEqual('UNKNOWN', first_pass_order.status)
        self.assertIn('absence_check:1', first_pass_order.failure_reason or '')

        portfolio.sync_from_broker()

        restored = positions.find_by_id(active.id)
        self.assertIsNotNone(restored)
        self.assertEqual('CLOSED', restored.status)
        self.assertEqual('broker_position_absent_after_sell', restored.exit_reason)
        saved_order = orders.find_by_id(unresolved.id)
        self.assertIsNotNone(saved_order)
        self.assertEqual('FILLED', saved_order.status)
        self.assertEqual(1, len(notifier.trade_recovery_payloads))
        self.assertEqual('SELL', notifier.trade_recovery_payloads[0]['side'])

    def test_portfolio_sync_records_estimated_entry_recovery_details(self) -> None:
        db_path = Path("data/test_fixture_estimated_entry_recovery.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)
        notifier = CapturingNotifier()

        class _HoldingClient(KISClientStub):
            def get_positions(self):
                return [BrokerPositionSnapshot(symbol="005930", qty=2, avg_price=70000.0, current_price=71000.0, name="Samsung")]

            def get_open_orders(self):
                return []

            def get_daily_fills(self):
                return []

        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _HoldingClient(),
            system_events,
            notifier,
        )
        position = Position(symbol='005930', qty=0, status='OPENING')
        positions.upsert(position)
        order = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='005930',
            side='BUY',
            qty=2,
            order_type='LIMIT',
            intent='ENTRY',
            position_id=position.id,
            broker_order_id='ORDER-EST-BUY',
            status='UNKNOWN',
            filled_qty=0,
            remaining_qty=2,
            price=70500.0,
        )
        orders.create(order)

        portfolio.record_estimated_entry_recovery(order, BrokerPositionSnapshot(symbol='005930', qty=2, avg_price=70000.0, current_price=71000.0, name='Samsung'), source='브로커 보유 기준 주문 복구')

        restored = positions.find_active_by_symbol('005930')
        self.assertIsNotNone(restored)
        self.assertEqual('OPEN', restored.status)
        self.assertEqual(70000.0, restored.avg_entry_price)
        with db.transaction() as connection:
            row = connection.execute("SELECT COUNT(*) AS cnt FROM trade_logs WHERE position_id = ?", (position.id,)).fetchone()
        self.assertEqual(1, row['cnt'])
        self.assertEqual(1, len(notifier.trade_recovery_payloads))
        self.assertTrue(notifier.trade_recovery_payloads[0]['estimated'])
        self.assertEqual(70000.0, notifier.trade_recovery_payloads[0]['price'])

    def test_portfolio_sync_applies_daily_fill_and_sends_trade_fill_notification(self) -> None:
        db_path = Path("data/test_fixture_sync_daily_fill_notify.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _DailyFillClient(KISClientStub):
            def get_positions(self):
                return [BrokerPositionSnapshot(symbol="005930", qty=2, avg_price=70000.0, current_price=71000.0, name="Samsung")]

            def get_daily_fills(self):
                return [
                    BrokerFillSnapshot(
                        order_no='ORDER-BUY-005930',
                        symbol='005930',
                        side='BUY',
                        fill_qty=2,
                        fill_price=70000.0,
                        filled_at='2026-03-20T09:10:00+09:00',
                    )
                ]

        notifier = CapturingNotifier()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _DailyFillClient(),
            system_events,
            notifier,
        )
        position = Position(symbol='005930', qty=0, status='OPENING')
        positions.upsert(position)
        order = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='005930',
            side='BUY',
            qty=2,
            order_type='LIMIT',
            intent='ENTRY',
            position_id=position.id,
            broker_order_id='ORDER-BUY-005930',
            status='SUBMITTED',
            filled_qty=0,
            remaining_qty=2,
            price=70000.0,
        )
        orders.create(order)

        portfolio.sync_from_broker()

        saved = orders.find_by_id(order.id)
        self.assertIsNotNone(saved)
        self.assertEqual('FILLED', saved.status)
        self.assertEqual(1, len(notifier.trade_fill_payloads))
        self.assertEqual('005930', notifier.trade_fill_payloads[0]['symbol'])
        self.assertEqual('ENTRY', notifier.trade_fill_payloads[0]['reason'])

    def test_force_sync_from_broker_closes_absent_and_restores_present_positions(self) -> None:
        db_path = Path("data/test_fixture_force_sync.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _ForceSyncClient(KISClientStub):
            def get_positions(self):
                return [
                    BrokerPositionSnapshot(symbol="005930", qty=2, avg_price=70000.0, current_price=71000.0, name="Samsung"),
                ]

        notifier = CapturingNotifier()
        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _ForceSyncClient(),
            system_events,
            notifier,
        )
        existing = Position(symbol="005930", qty=0, status="CLOSED")
        positions.upsert(existing)
        recovered_order = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='005930',
            side='BUY',
            qty=2,
            order_type='LIMIT',
            intent='ENTRY',
            position_id=existing.id,
            broker_order_id='BUY-005930',
            status='UNKNOWN',
            filled_qty=0,
            remaining_qty=2,
            price=70000.0,
        )
        orders.create(recovered_order)
        stale = Position(symbol="006360", qty=10, status="OPEN", current_price=30000.0)
        positions.upsert(stale)
        stale_order = __import__('auto_trading.orders.models', fromlist=['Order']).Order(
            symbol='006360',
            side='SELL',
            qty=10,
            order_type='LIMIT',
            intent='TAKE_PROFIT',
            position_id=stale.id,
            broker_order_id='SELL-006360',
            status='UNKNOWN',
            filled_qty=0,
            remaining_qty=10,
            price=30000.0,
        )
        orders.create(stale_order)

        result = portfolio.force_sync_from_broker()

        recovered = positions.find_active_by_symbol('005930')
        self.assertIsNotNone(recovered)
        self.assertEqual(2, recovered.qty)
        closed = positions.find_by_id(stale.id)
        self.assertIsNotNone(closed)
        self.assertEqual('CLOSED', closed.status)
        self.assertEqual(0, closed.qty)
        self.assertEqual('force_broker_sync_absent', closed.exit_reason)
        saved_order = orders.find_by_id(stale_order.id)
        self.assertIsNotNone(saved_order)
        self.assertEqual('FILLED', saved_order.status)
        self.assertIn('005930', result['broker_symbols'])
        self.assertIn('006360', result['closed_symbols'])
        self.assertEqual(2, len(notifier.trade_recovery_payloads))
        joined_symbols = {item['symbol'] for item in notifier.trade_recovery_payloads}
        self.assertIn('005930', joined_symbols)
        self.assertIn('006360', joined_symbols)

    def test_force_sync_from_broker_aborts_on_empty_holdings_by_default(self) -> None:
        db_path = Path("data/test_fixture_force_sync_empty_abort.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _EmptyClient(KISClientStub):
            def get_positions(self):
                return []

        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _EmptyClient(),
            system_events,
        )
        stale = Position(symbol="006360", qty=10, status="OPEN")
        positions.upsert(stale)

        result = portfolio.force_sync_from_broker()

        self.assertFalse(result['applied'])
        self.assertEqual('empty_broker_positions', result['aborted_reason'])
        still_open = positions.find_active_by_symbol('006360')
        self.assertIsNotNone(still_open)
        self.assertEqual(10, still_open.qty)

    def test_force_sync_from_broker_dry_run_does_not_modify_positions(self) -> None:
        db_path = Path("data/test_fixture_force_sync_dry_run.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _ForceSyncClient(KISClientStub):
            def get_positions(self):
                return []

        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _ForceSyncClient(),
            system_events,
        )
        stale = Position(symbol="006360", qty=10, status="OPEN")
        positions.upsert(stale)

        result = portfolio.force_sync_from_broker(dry_run=True, allow_empty=True, confirm_rounds=1)

        self.assertFalse(result['applied'])
        self.assertTrue(result['dry_run'])
        self.assertIn('006360', result['closed_symbols'])
        still_open = positions.find_active_by_symbol('006360')
        self.assertIsNotNone(still_open)
        self.assertEqual(10, still_open.qty)

    def test_force_sync_from_broker_aborts_on_unstable_holdings(self) -> None:
        db_path = Path("data/test_fixture_force_sync_unstable.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        orders = OrdersRepository(db)
        positions = PositionsRepository(db)
        system_events = SystemEventsRepository(db)

        class _UnstableClient(KISClientStub):
            def __init__(self) -> None:
                super().__init__()
                self._calls = 0

            def get_positions(self):
                self._calls += 1
                if self._calls == 1:
                    return [BrokerPositionSnapshot(symbol="005930", qty=2, avg_price=70000.0, current_price=71000.0, name="Samsung")]
                return []

        portfolio = PortfolioService(
            positions,
            orders,
            FillsRepository(db),
            TradeLogsRepository(db),
            _UnstableClient(),
            system_events,
        )

        result = portfolio.force_sync_from_broker(confirm_rounds=2, allow_empty=True)

        self.assertFalse(result['applied'])
        self.assertEqual('unstable_broker_positions', result['aborted_reason'])

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


    def test_websocket_client_dedupes_quote_subscriptions_per_connection(self) -> None:
        class _Socket:
            def __init__(self) -> None:
                self.sent: list[str] = []

            def send(self, payload: str) -> None:
                self.sent.append(payload)

            def close(self) -> None:
                return None

        client = KISWebSocketClient(build_settings(), KISClientStub())
        client._approval_key = "approval"
        client._socket = _Socket()
        client.subscribe_quotes(["005930", "005930", "088350"])
        client.subscribe_quotes(["005930", "088350"])
        self.assertEqual(["005930", "088350"], client.subscribed_symbols)
        self.assertEqual(2, len(client._socket.sent))

    def test_websocket_client_reconnects_when_quote_subscription_socket_is_closed(self) -> None:
        class _Socket:
            def __init__(self, *, fail_first_send: bool = False) -> None:
                self.sent: list[str] = []
                self.fail_first_send = fail_first_send
                self._send_count = 0

            def send(self, payload: str) -> None:
                self._send_count += 1
                if self.fail_first_send and self._send_count == 1:
                    raise RuntimeError('socket is already closed.')
                self.sent.append(payload)

            def close(self) -> None:
                return None

        class _ReconnectClient(KISWebSocketClient):
            __slots__ = ('_test_sockets',)

            def connect(self) -> None:
                self._approval_key = 'approval'
                self._active_quote_subscriptions.clear()
                self._socket = self._test_sockets.pop(0)

        first_socket = _Socket(fail_first_send=True)
        second_socket = _Socket()
        client = _ReconnectClient(build_settings(), KISClientStub())
        client._test_sockets = [second_socket]
        client._approval_key = 'approval'
        client._socket = first_socket
        client.subscribe_quotes(['005930'])

        self.assertEqual(['005930'], client.subscribed_symbols)
        self.assertEqual(0, len(first_socket.sent))
        self.assertEqual(2, len(second_socket.sent))

    def test_websocket_client_resubscribes_after_disconnect(self) -> None:
        class _Socket:
            def __init__(self) -> None:
                self.sent: list[str] = []

            def send(self, payload: str) -> None:
                self.sent.append(payload)

            def close(self) -> None:
                return None

        client = KISWebSocketClient(build_settings(), KISClientStub())
        first_socket = _Socket()
        client._approval_key = "approval"
        client._socket = first_socket
        client.subscribe_quotes(["005930"])
        client.disconnect()
        second_socket = _Socket()
        client._approval_key = "approval"
        client._socket = second_socket
        client.subscribe_quotes(["005930"])
        self.assertEqual(1, len(first_socket.sent))
        self.assertEqual(1, len(second_socket.sent))

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
