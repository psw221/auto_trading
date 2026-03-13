from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_trading.config.schema import Settings
from auto_trading.notifications.telegram import TelegramNotifier
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.system_events import SystemEventsRepository


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _build_settings(
    *,
    token: str = "bot-token",
    chat_id: str = "123456",
    universe_master_path: Path | None = None,
) -> Settings:
    return Settings(
        env="demo",
        db_path=Path("data/test_telegram_notifier.db"),
        kis_base_url="https://example.com",
        kis_ws_url="ws://example.com",
        kis_app_key="key",
        kis_app_secret="secret",
        kis_cano="123",
        kis_acnt_prdt_cd="01",
        kis_access_token="token",
        kis_refresh_token="",
        kis_user_id="user1",
        universe_master_path=universe_master_path or Path("data/universe_master.csv"),
        holiday_calendar_path=Path("data/krx_holidays.csv"),
        holiday_api_service_key="",
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
    )


class TelegramNotifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data/test_telegram_notifier_runtime.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.db = Database(self.db_path)
        self.db.initialize()
        self.system_events = SystemEventsRepository(self.db)

    def test_send_trade_fill_posts_to_telegram(self) -> None:
        notifier = TelegramNotifier(
            _build_settings(universe_master_path=Path("data/universe_master.sample.csv")),
            self.system_events,
        )
        with patch("auto_trading.notifications.telegram.request.urlopen", return_value=_FakeResponse({"ok": True})) as mocked:
            notifier.send_trade_fill(
                {
                    "symbol": "005930",
                    "symbol_name": "",
                    "side": "BUY",
                    "reason": "ENTRY",
                    "fill_qty": 1,
                    "fill_price": 70000,
                    "filled_qty": 1,
                    "total_qty": 3,
                    "remaining_qty": 2,
                    "position_qty": 1,
                    "filled_at": "2026-03-12T09:01:00+09:00",
                }
            )
        mocked.assert_called_once()
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT event_type, message FROM system_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual("trade_fill_notification_sent", row["event_type"])
        self.assertIn("Samsung Electronics (005930)", row["message"])
        self.assertIn("사유: 전략 진입", row["message"])
        self.assertIn("주문 진행: 1/3주 체결", row["message"])

    def test_send_system_event_skips_when_credentials_missing(self) -> None:
        notifier = TelegramNotifier(_build_settings(token="", chat_id=""), self.system_events)
        notifier.send_system_event({"message": "stream disconnected", "severity": "ERROR", "component": "runtime"})
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT event_type, message FROM system_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual("notification_skipped", row["event_type"])
        self.assertIn("not configured", row["message"])


if __name__ == "__main__":
    unittest.main()
