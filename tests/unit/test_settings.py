from __future__ import annotations

import os
import unittest
from pathlib import Path

from auto_trading.config.settings import load_settings


class LoadSettingsTest(unittest.TestCase):
    def test_load_settings_reads_dotenv_file(self) -> None:
        env_path = Path("data/test_settings.env")
        if env_path.exists():
            env_path.unlink()
        env_path.write_text(
            "\n".join(
                [
                    "AUTO_TRADING_ENV=real",
                    "AUTO_TRADING_DB_PATH=./data/runtime.db",
                    'AUTO_TRADING_TELEGRAM_CHAT_ID="123456"',
                ]
            ),
            encoding="utf-8",
        )
        original_env = os.environ.copy()
        try:
            os.environ.pop("AUTO_TRADING_ENV", None)
            os.environ.pop("AUTO_TRADING_DB_PATH", None)
            os.environ.pop("AUTO_TRADING_TELEGRAM_CHAT_ID", None)
            settings = load_settings(env_path=env_path)
        finally:
            os.environ.clear()
            os.environ.update(original_env)
            if env_path.exists():
                env_path.unlink()
        self.assertEqual("real", settings.env)
        self.assertEqual(Path("data/runtime.db"), settings.db_path)
        self.assertEqual("123456", settings.telegram_chat_id)

    def test_existing_environment_value_wins_over_dotenv(self) -> None:
        env_path = Path("data/test_settings.env")
        if env_path.exists():
            env_path.unlink()
        env_path.write_text("AUTO_TRADING_ENV=real", encoding="utf-8")
        original_env = os.environ.copy()
        try:
            os.environ["AUTO_TRADING_ENV"] = "demo"
            settings = load_settings(env_path=env_path)
        finally:
            os.environ.clear()
            os.environ.update(original_env)
            if env_path.exists():
                env_path.unlink()
        self.assertEqual("demo", settings.env)

    def test_blank_kis_urls_fall_back_to_environment_defaults(self) -> None:
        env_path = Path("data/test_settings.env")
        if env_path.exists():
            env_path.unlink()
        env_path.write_text(
            "\n".join(
                [
                    "AUTO_TRADING_ENV=demo",
                    "AUTO_TRADING_KIS_BASE_URL=",
                    "AUTO_TRADING_KIS_WS_URL=",
                ]
            ),
            encoding="utf-8",
        )
        original_env = os.environ.copy()
        try:
            os.environ.pop("AUTO_TRADING_ENV", None)
            os.environ.pop("AUTO_TRADING_KIS_BASE_URL", None)
            os.environ.pop("AUTO_TRADING_KIS_WS_URL", None)
            settings = load_settings(env_path=env_path)
        finally:
            os.environ.clear()
            os.environ.update(original_env)
            if env_path.exists():
                env_path.unlink()
        self.assertEqual("https://openapivts.koreainvestment.com:29443", settings.kis_base_url)
        self.assertEqual("ws://ops.koreainvestment.com:31000", settings.kis_ws_url)


if __name__ == "__main__":
    unittest.main()
