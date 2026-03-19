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

    def exists_for_report_date(self, event_type: str, report_date: str) -> bool:
        if not report_date:
            return False
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM system_events
                WHERE event_type = ?
                ORDER BY id DESC
                LIMIT 200
                """,
                (event_type,),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if str(payload.get("report_date", "")).strip() == report_date:
                return True
        return False
