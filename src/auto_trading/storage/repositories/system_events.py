from __future__ import annotations

import json
from dataclasses import dataclass

from auto_trading.common.time import utc_now


@dataclass(slots=True)
class SystemEventsRepository:
    db: object

    def create(
        self,
        event_type: str,
        severity: str,
        component: str,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> int:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO system_events (
                    event_type,
                    severity,
                    component,
                    message,
                    payload_json,
                    occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    severity,
                    component,
                    message,
                    json.dumps(payload or {}, ensure_ascii=True),
                    utc_now().isoformat(),
                ),
            )
        return int(cursor.lastrowid)
