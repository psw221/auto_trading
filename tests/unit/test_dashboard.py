from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from auto_trading.app.dashboard import build_dashboard_summary, build_strategy_targets_summary, format_dashboard_summary, format_strategy_targets_summary
from auto_trading.storage.db import Database


class DashboardSummaryTest(unittest.TestCase):
    def test_build_dashboard_summary_reports_key_counts(self) -> None:
        db_path = Path("data/test_dashboard.db")
        master_path = Path("data/test_dashboard_universe.csv")
        if db_path.exists():
            db_path.unlink()
        master_path.write_text(
            "symbol,name,market,asset_type\n005930,Samsung Electronics,KOSPI,STOCK\n",
            encoding="utf-8",
        )
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
            connection.execute(
                """
                INSERT INTO system_events (
                    event_type, severity, component, message, payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "market_scan_summary",
                    "INFO",
                    "scheduler",
                    "Recorded latest market scan summary.",
                    '{"universe_count": 50, "scored_count": 18, "qualified_count": 3, "top_candidate_count": 10, "snapshot_time": "2026-03-13T00:20:00+00:00"}',
                    "2026-03-13T00:20:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO strategy_snapshots (
                    symbol, snapshot_time, score_total, volume_score, momentum_score, ma_score,
                    atr_score, rsi_score, price, ma5, ma20, rsi, atr, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "005930",
                    "2026-03-13T00:20:00+00:00",
                    85,
                    20,
                    20,
                    40,
                    10,
                    10,
                    71000,
                    70500,
                    69000,
                    58,
                    2.1,
                    '{"momentum_20": 8.2, "volume_ratio": 1.6}',
                    "2026-03-13T00:20:00+00:00",
                ),
            )
        now = datetime(2026, 3, 13, 9, 30, tzinfo=timezone.utc)
        summary = build_dashboard_summary(db_path, master_path, now=now)
        self.assertTrue(summary.db_exists)
        self.assertEqual(1, summary.active_positions)
        self.assertEqual(1, summary.error_positions)
        self.assertEqual(1, summary.unknown_orders)
        self.assertEqual(1, summary.open_orders)
        self.assertEqual(1, len(summary.recent_fills))
        self.assertEqual(1, len(summary.recent_errors))
        self.assertEqual(1, len(summary.tracked_positions))
        self.assertEqual('005930', summary.tracked_positions[0]['symbol'])
        self.assertEqual(1, len(summary.today_targets))
        self.assertEqual("Samsung Electronics", summary.today_targets[0]["name"])
        self.assertEqual(50, summary.latest_market_scan["universe_count"])
        self.assertEqual(18, summary.latest_market_scan["scored_count"])
        rendered = format_dashboard_summary(summary, db_path)
        self.assertIn("active_positions=1", rendered)
        self.assertIn("[latest_market_scan]", rendered)
        self.assertIn("qualified_count=3", rendered)
        self.assertIn("[tracked_positions]", rendered)
        self.assertIn("status=OPEN", rendered)
        self.assertIn("[today_targets]", rendered)
        self.assertIn("Samsung Electronics", rendered)
        targets_summary = build_strategy_targets_summary(db_path, master_path, now=now)
        targets_rendered = format_strategy_targets_summary(targets_summary, db_path)
        self.assertIn("target_date=2026-03-13", targets_rendered)
        self.assertIn("score_total=85", targets_rendered)


if __name__ == "__main__":
    unittest.main()
