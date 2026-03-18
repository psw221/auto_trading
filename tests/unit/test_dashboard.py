from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from auto_trading.app.dashboard import build_daily_report_summary, build_dashboard_summary, build_strategy_targets_summary, format_daily_report_summary, format_dashboard_summary, format_strategy_targets_summary
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
                (1, "B1", "005930", "BUY", 70000, 1, 70000, "2026-03-13T09:02:00+09:00", "2026-03-13T09:02:00+09:00"),
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
            connection.execute(
                """
                INSERT INTO trade_logs (
                    position_id, symbol, strategy_name, entry_order_id, exit_order_id,
                    entry_price, exit_price, qty, gross_pnl, net_pnl, pnl_pct, entry_at, exit_at, exit_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "005930",
                    "swing",
                    1,
                    1,
                    70000,
                    71500,
                    2,
                    3000,
                    3000,
                    2.14,
                    "2026-03-12T09:00:00+09:00",
                    "2026-03-13T14:50:00+09:00",
                    "TAKEPROFIT",
                    "2026-03-13T14:50:00+09:00",
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
        daily_summary = build_daily_report_summary(db_path, master_path, now=now)
        self.assertTrue(daily_summary.db_exists)
        self.assertEqual(1, daily_summary.active_positions)
        self.assertEqual(1, daily_summary.today_fill_count)
        self.assertEqual(['005930'], daily_summary.traded_symbols)
        self.assertEqual(3000.0, daily_summary.realized_pnl)
        self.assertEqual(2000.0, daily_summary.unrealized_pnl)
        self.assertEqual(5000.0, daily_summary.total_pnl)
        self.assertEqual(1, daily_summary.closed_trade_count)
        self.assertEqual(1, daily_summary.winning_trade_count)
        self.assertAlmostEqual(1.0, daily_summary.win_rate)
        self.assertAlmostEqual(2.14, daily_summary.average_closed_pnl_pct)
        self.assertEqual('005930', daily_summary.best_trade['symbol'])
        self.assertEqual('005930', daily_summary.worst_trade['symbol'])
        daily_rendered = format_daily_report_summary(daily_summary)
        self.assertIn('[AUTO_TRADING] 일일 리포트', daily_rendered)
        self.assertIn('실현손익: +3,000원', daily_rendered)
        self.assertIn('미실현손익: +2,000원', daily_rendered)
        self.assertIn('총손익: +5,000원', daily_rendered)
        self.assertIn('승률: 100.0%', daily_rendered)
        self.assertIn('[청산 내역]', daily_rendered)
        self.assertIn('사유=익절', daily_rendered)
        self.assertIn('Samsung Electronics(005930)', daily_rendered)

    def test_build_dashboard_summary_dedupes_active_positions_by_symbol(self) -> None:
        db_path = Path("data/test_dashboard_duplicates.db")
        master_path = Path("data/test_dashboard_duplicates_universe.csv")
        if db_path.exists():
            db_path.unlink()
        master_path.write_text(
            "symbol,name,market,asset_type\n"
            "005440,현대지에프홀딩스,KOSPI,STOCK\n"
            "088350,한화생명,KOSPI,STOCK\n",
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
                ("005440", "현대지에프홀딩스", "swing", "OPEN", 161, 15471.428, 15600, 80, None, "2026-03-17T09:00:00+09:00", None, None, "2026-03-17T09:00:00+09:00", "2026-03-17T09:01:00+09:00"),
            )
            connection.execute(
                """
                INSERT INTO positions (
                    symbol, name, strategy_name, status, qty, avg_entry_price, current_price,
                    score_at_entry, target_weight, opened_at, closed_at, exit_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("005440", "현대지에프홀딩스", "swing", "OPEN", 161, 15471.428, 15600, 80, None, "2026-03-17T09:00:00+09:00", None, None, "2026-03-17T09:00:00+09:00", "2026-03-17T09:00:30+09:00"),
            )
            connection.execute(
                """
                INSERT INTO positions (
                    symbol, name, strategy_name, status, qty, avg_entry_price, current_price,
                    score_at_entry, target_weight, opened_at, closed_at, exit_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("088350", "한화생명", "swing", "OPEN", 527, 4735, 4705, 80, None, "2026-03-17T09:00:00+09:00", None, None, "2026-03-17T09:00:00+09:00", "2026-03-17T09:02:00+09:00"),
            )
        summary = build_dashboard_summary(db_path, master_path)
        self.assertEqual(2, summary.active_positions)
        self.assertEqual(2, len(summary.tracked_positions))
        symbols = sorted(item['symbol'] for item in summary.tracked_positions)
        self.assertEqual(['005440', '088350'], symbols)


    def test_build_daily_report_summary_includes_broker_hhmmss_times(self) -> None:
        db_path = Path("data/test_dashboard_broker_times.db")
        master_path = Path("data/test_dashboard_broker_times_universe.csv")
        if db_path.exists():
            db_path.unlink()
        master_path.write_text(
            "symbol,name,market,asset_type\n"
            "088350,한화생명,KOSPI,STOCK\n",
            encoding="utf-8",
        )
        db = Database(db_path)
        db.initialize()
        with db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO positions (
                    id, symbol, name, strategy_name, status, qty, avg_entry_price, current_price,
                    score_at_entry, target_weight, opened_at, closed_at, exit_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "088350", "한화생명", "swing", "CLOSED", 0, 4735, 5200, 80, None, "2026-03-17T14:35:01+09:00", "2026-03-18T09:48:58+09:00", "TAKE_PROFIT", "2026-03-17T14:35:01+09:00", "2026-03-18T09:48:58+09:00"),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    id, client_order_id, broker_order_id, position_id, symbol, side, order_type, intent,
                    price, qty, filled_qty, remaining_qty, status, submitted_at, last_broker_update_at,
                    failure_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "order-1", "B1", 1, "088350", "BUY", "LIMIT", "ENTRY", 4735, 527, 527, 0, "FILLED", "2026-03-17T05:35:01+00:00", "2026-03-17T05:35:02+00:00", None, "2026-03-17T05:35:01+00:00", "2026-03-17T05:35:02+00:00"),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    id, client_order_id, broker_order_id, position_id, symbol, side, order_type, intent,
                    price, qty, filled_qty, remaining_qty, status, submitted_at, last_broker_update_at,
                    failure_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (11, "order-11", "S1", 1, "088350", "SELL", "LIMIT", "TAKE_PROFIT", 5200, 527, 527, 0, "FILLED", "2026-03-18T00:48:57+00:00", "2026-03-18T00:48:58+00:00", None, "2026-03-18T00:48:57+00:00", "2026-03-18T00:48:58+00:00"),
            )
            connection.execute(
                """
                INSERT INTO fills (
                    order_id, broker_fill_id, symbol, side, fill_price, fill_qty, fill_amount, filled_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (11, "S1", "088350", "SELL", 5200, 527, 2740400, "094858", "2026-03-18T00:48:58+00:00"),
            )
            connection.execute(
                """
                INSERT INTO trade_logs (
                    position_id, symbol, strategy_name, entry_order_id, exit_order_id,
                    entry_price, exit_price, qty, gross_pnl, net_pnl, pnl_pct, entry_at, exit_at, exit_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "088350",
                    "swing",
                    1,
                    11,
                    4735,
                    5200,
                    527,
                    245055,
                    245055,
                    9.82,
                    "090001",
                    "094858",
                    "TAKE_PROFIT",
                    "2026-03-17T00:10:00+00:00",
                ),
            )
        now = datetime.fromisoformat("2026-03-18T16:30:00+09:00")
        summary = build_daily_report_summary(db_path, master_path, now=now)
        self.assertEqual(1, summary.today_fill_count)
        self.assertEqual(['088350'], summary.traded_symbols)
        self.assertEqual(1, summary.closed_trade_count)
        self.assertEqual(245055.0, summary.realized_pnl)
        rendered = format_daily_report_summary(summary)
        self.assertIn('한화생명(088350) SELL 527주 @ 5,200원 (094858)', rendered)
        self.assertIn('실현손익: +245,055원', rendered)

    def test_build_daily_report_summary_excludes_old_hhmmss_records(self) -> None:
        db_path = Path("data/test_dashboard_old_hhmmss.db")
        master_path = Path("data/test_dashboard_old_hhmmss_universe.csv")
        if db_path.exists():
            db_path.unlink()
        master_path.write_text(
            "symbol,name,market,asset_type\n"
            "034230,파라다이스,KOSPI,STOCK\n",
            encoding="utf-8",
        )
        db = Database(db_path)
        db.initialize()
        with db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO positions (
                    id, symbol, name, strategy_name, status, qty, avg_entry_price, current_price,
                    score_at_entry, target_weight, opened_at, closed_at, exit_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "034230", "파라다이스", "swing", "CLOSED", 0, 17450, 17390, 80, None, "2026-03-16T10:07:53+09:00", "2026-03-16T10:08:43+09:00", "MA5_BREAKDOWN", "2026-03-16T01:07:53+00:00", "2026-03-16T01:08:43+00:00"),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    id, client_order_id, broker_order_id, position_id, symbol, side, order_type, intent,
                    price, qty, filled_qty, remaining_qty, status, submitted_at, last_broker_update_at,
                    failure_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "order-1", "B1", 1, "034230", "BUY", "LIMIT", "ENTRY", 17450, 143, 143, 0, "FILLED", "2026-03-16T01:07:53+00:00", "2026-03-16T01:07:54+00:00", None, "2026-03-16T01:07:53+00:00", "2026-03-16T01:07:54+00:00"),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    id, client_order_id, broker_order_id, position_id, symbol, side, order_type, intent,
                    price, qty, filled_qty, remaining_qty, status, submitted_at, last_broker_update_at,
                    failure_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (2, "order-2", "S2", 1, "034230", "SELL", "MARKET", "MA5_BREAKDOWN", None, 143, 143, 0, "FILLED", "2026-03-16T01:08:42+00:00", "2026-03-16T01:08:43+00:00", None, "2026-03-16T01:08:42+00:00", "2026-03-16T01:08:43+00:00"),
            )
            connection.execute(
                """
                INSERT INTO fills (
                    order_id, broker_fill_id, symbol, side, fill_price, fill_qty, fill_amount, filled_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (2, "S2", "034230", "SELL", 17390, 143, 2486770, "100843", "2026-03-16T01:08:43+00:00"),
            )
            connection.execute(
                """
                INSERT INTO trade_logs (
                    position_id, symbol, strategy_name, entry_order_id, exit_order_id,
                    entry_price, exit_price, qty, gross_pnl, net_pnl, pnl_pct, entry_at, exit_at, exit_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "034230", "swing", 1, 2, 17450, 17390, 143, -8580, -8580, -0.34, "100753", "100843", "MA5_BREAKDOWN", "2026-03-16T01:07:54+00:00"),
            )
        now = datetime.fromisoformat("2026-03-18T16:30:00+09:00")
        summary = build_daily_report_summary(db_path, master_path, now=now)
        self.assertEqual(0, summary.today_fill_count)
        self.assertEqual([], summary.traded_symbols)
        self.assertEqual(0, summary.closed_trade_count)
        self.assertEqual(0.0, summary.realized_pnl)

if __name__ == "__main__":
    unittest.main()






