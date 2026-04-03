from __future__ import annotations

import os
from pathlib import Path

from auto_trading.config.schema import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _default_kis_base_url(env: str) -> str:
    if env == "real":
        return "https://openapi.koreainvestment.com:9443"
    return "https://openapivts.koreainvestment.com:29443"


def _default_kis_ws_url(env: str) -> str:
    if env == "real":
        return "ws://ops.koreainvestment.com:21000"
    return "ws://ops.koreainvestment.com:31000"


def load_settings(env_path: Path | None = None) -> Settings:
    _load_dotenv(env_path)
    env = os.getenv("AUTO_TRADING_ENV", "demo")
    return Settings(
        env=env,
        db_path=Path(_getenv("AUTO_TRADING_DB_PATH", "./data/auto_trading.db")),
        kis_base_url=_getenv("AUTO_TRADING_KIS_BASE_URL", _default_kis_base_url(env)),
        kis_ws_url=_getenv("AUTO_TRADING_KIS_WS_URL", _default_kis_ws_url(env)),
        kis_app_key=_getenv("AUTO_TRADING_KIS_APP_KEY", ""),
        kis_app_secret=_getenv("AUTO_TRADING_KIS_APP_SECRET", ""),
        kis_cano=_getenv("AUTO_TRADING_KIS_CANO", ""),
        kis_acnt_prdt_cd=_getenv("AUTO_TRADING_KIS_ACNT_PRDT_CD", ""),
        kis_access_token=_getenv("AUTO_TRADING_KIS_ACCESS_TOKEN", ""),
        kis_refresh_token=_getenv("AUTO_TRADING_KIS_REFRESH_TOKEN", ""),
        kis_user_id=_getenv("AUTO_TRADING_KIS_USER_ID", ""),
        universe_master_path=Path(_getenv("AUTO_TRADING_UNIVERSE_MASTER_PATH", "./data/universe_master.csv")),
        holiday_calendar_path=Path(_getenv("AUTO_TRADING_HOLIDAY_CALENDAR_PATH", "./data/krx_holidays.csv")),
        holiday_api_service_key=_getenv("AUTO_TRADING_HOLIDAY_API_SERVICE_KEY", ""),
        rest_min_interval_seconds=_getenv_float("AUTO_TRADING_REST_MIN_INTERVAL_SECONDS", 0.12),
        telegram_bot_token=_getenv("AUTO_TRADING_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_getenv("AUTO_TRADING_TELEGRAM_CHAT_ID", ""),
        telegram_notify_trade_fill=_getenv_bool("AUTO_TRADING_TELEGRAM_NOTIFY_TRADE_FILL", True),
        telegram_notify_trade_recovery=_getenv_bool("AUTO_TRADING_TELEGRAM_NOTIFY_TRADE_RECOVERY", True),
        telegram_notify_target_scores=_getenv_bool("AUTO_TRADING_TELEGRAM_NOTIFY_TARGET_SCORES", False),
        telegram_notify_system_event=_getenv_bool("AUTO_TRADING_TELEGRAM_NOTIFY_SYSTEM_EVENT", True),
        telegram_notify_daily_report=_getenv_bool("AUTO_TRADING_TELEGRAM_NOTIFY_DAILY_REPORT", True),
    )


def _load_dotenv(env_path: Path | None = None, *, override: bool = False) -> None:
    target = env_path or PROJECT_ROOT / ".env"
    if not target.exists():
        return
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _normalize_env_value(value.strip())


def _normalize_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _getenv(key: str, default: str) -> str:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value


def _getenv_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _getenv_float(key: str, default: float) -> float:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default

