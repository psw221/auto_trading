from __future__ import annotations

import unittest
from pathlib import Path

from auto_trading.app.dashboard import build_dashboard_summary, format_dashboard_summary
from auto_trading.storage.db import Database


class DashboardSummaryTest(unittest.TestCase):
    def test_build_dashboard_summary_reports_key_counts(self) -> None:
        db_path = Path("data/test_dashboard.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        with db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO positions (
                    symbol, name, strategy_name, status, qty, avg_entry_price, current_price,
                    score_at_entry, target_weight, opened_at, closed_at, exit_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("005930", "Samsung", "swing", "OPEN", 2, 70000, 71000, 80, None, "2026-03-12T09:00:00+09:00", None, None, "2026-03-12T09:00:00+09:00", "2026-03-12T09:01:00+09:00"),
            )
            connection.execute(
                """
                INSERT INTO positions (
                    symbol, name, strategy_name, status, qty, avg_entry_price, current_price,
                    score_at_entry, target_weight, opened_at, closed_at, exit_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("000660", "SK Hynix", "swing", "ERROR", 1, 120000, 119000, 75, None, "2026-03-12T09:00:00+09:00", None, "sync_error", "2026-03-12T09:00:00+09:00", "2026-03-12T09:02:00+09:00"),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    client_order_id, broker_order_id, position_id, symbol, side, order_type, intent,
                    price, qty, filled_qty, remaining_qty, status, submitted_at, last_broker_update_at,
                    failure_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("order-1", "B1", 1, "005930", "BUY", "LIMIT", "ENTRY", 70000, 2, 0, 2, "SUBMITTED", None, None, None, "2026-03-12T09:00:00+09:00", "2026-03-12T09:00:30+09:00"),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    client_order_id, broker_order_id, position_id, symbol, side, order_type, intent,
                    price, qty, filled_qty, remaining_qty, status, submitted_at, last_broker_update_at,
                    failure_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("order-2", "B2", 2, "000660", "BUY", "LIMIT", "ENTRY", 120000, 1, 0, 1, "UNKNOWN", None, None, "timeout", "2026-03-12T09:01:00+09:00", "2026-03-12T09:01:30+09:00"),
            )
            connection.execute(
                """
                INSERT INTO fills (
                    order_id, broker_fill_id, symbol, side, fill_price, fill_qty, fill_amount, filled_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "B1", "005930", "BUY", 70000, 1, 70000, "2026-03-12T09:02:00+09:00", "2026-03-12T09:02:00+09:00"),
            )
            connection.execute(
                """
                INSERT INTO system_events (
                    event_type, severity, component, message, payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("broker_exception", "ERROR", "orders.engine", "network down", "{}", "2026-03-12T09:03:00+09:00"),
            )
        summary = build_dashboard_summary(db_path)
        self.assertTrue(summary.db_exists)
        self.assertEqual(1, summary.active_positions)
        self.assertEqual(1, summary.error_positions)
        self.assertEqual(1, summary.unknown_orders)
        self.assertEqual(1, summary.open_orders)
        self.assertEqual(1, len(summary.recent_fills))
        self.assertEqual(1, len(summary.recent_errors))
        rendered = format_dashboard_summary(summary, db_path)
        self.assertIn("active_positions=1", rendered)
        self.assertIn("[recent_errors]", rendered)


if __name__ == "__main__":
    unittest.main()
