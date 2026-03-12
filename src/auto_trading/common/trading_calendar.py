from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path


@dataclass(slots=True)
class TradingCalendar:
    holiday_path: Path
    _holidays: set[date] = field(init=False, default_factory=set)
    _loaded: bool = field(init=False, default=False)

    def is_trading_day(self, value: datetime | date) -> bool:
        day = value.date() if isinstance(value, datetime) else value
        if day.weekday() >= 5:
            return False
        self._ensure_loaded()
        return day not in self._holidays

    def load(self) -> None:
        self._holidays.clear()
        if self.holiday_path.exists():
            with self.holiday_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    raw = (row.get("date") or "").strip()
                    if not raw:
                        continue
                    self._holidays.add(date.fromisoformat(raw))
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()
