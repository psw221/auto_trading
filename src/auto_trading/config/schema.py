from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    env: str
    db_path: Path
    kis_base_url: str
    kis_ws_url: str
    kis_app_key: str
    kis_app_secret: str
    kis_cano: str
    kis_acnt_prdt_cd: str
    kis_access_token: str
    kis_refresh_token: str
    kis_user_id: str
    universe_master_path: Path
    holiday_calendar_path: Path
    holiday_api_service_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    max_positions: int = 3
    base_weight: float = 0.25
    max_weight: float = 0.30
    min_cash_weight: float = 0.10
