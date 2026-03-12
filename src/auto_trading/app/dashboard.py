from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DashboardSummary:
    db_exists: bool
    active_positions: int
    opening_positions: int
    closing_positions: int
    error_positions: int
    unknown_orders: int
    open_orders: int
    recent_fills: list[dict[str, object]]
    recent_orders: list[dict[str, object]]
    recent_errors: list[dict[str, object]]


def build_dashboard_summary(db_path: Path) -> DashboardSummary:
    if not db_path.exists():
        return DashboardSummary(
            db_exists=False,
            active_positions=0,
            opening_positions=0,
            closing_positions=0,
            error_positions=0,
            unknown_orders=0,
            open_orders=0,
            recent_fills=[],
            recent_orders=[],
            recent_errors=[],
        )

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        active_positions = _count_positions(connection, ("OPENING", "OPEN", "CLOSING"))
        opening_positions = _count_positions(connection, ("OPENING",))
        closing_positions = _count_positions(connection, ("CLOSING",))
        error_positions = _count_positions(connection, ("ERROR",))
        unknown_orders = _count_orders(connection, ("UNKNOWN",))
        open_orders = _count_orders(
            connection,
            ("PENDING_CREATE", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "PENDING_REPLACE", "REPLACED", "PENDING_CANCEL"),
        )
        recent_fills = _fetch_rows(
            connection,
            """
            SELECT symbol, side, fill_qty, fill_price, filled_at
            FROM fills
            ORDER BY filled_at DESC, id DESC
            LIMIT 5
            """,
        )
        recent_orders = _fetch_rows(
            connection,
            """
            SELECT symbol, side, qty, filled_qty, remaining_qty, status, broker_order_id, updated_at
            FROM orders
            ORDER BY updated_at DESC, id DESC
            LIMIT 10
            """,
        )
        recent_errors = _fetch_rows(
            connection,
            """
            SELECT severity, component, event_type, message, occurred_at
            FROM system_events
            WHERE severity IN ('ERROR', 'CRITICAL')
            ORDER BY occurred_at DESC, id DESC
            LIMIT 10
            """,
        )
    finally:
        connection.close()

    return DashboardSummary(
        db_exists=True,
        active_positions=active_positions,
        opening_positions=opening_positions,
        closing_positions=closing_positions,
        error_positions=error_positions,
        unknown_orders=unknown_orders,
        open_orders=open_orders,
        recent_fills=recent_fills,
        recent_orders=recent_orders,
        recent_errors=recent_errors,
    )


def format_dashboard_summary(summary: DashboardSummary, db_path: Path) -> str:
    lines = [f"db_path={db_path}"]
    if not summary.db_exists:
        lines.append("db_exists=False")
        return "\n".join(lines)

    lines.extend(
        [
            "db_exists=True",
            f"active_positions={summary.active_positions}",
            f"opening_positions={summary.opening_positions}",
            f"closing_positions={summary.closing_positions}",
            f"error_positions={summary.error_positions}",
            f"unknown_orders={summary.unknown_orders}",
            f"open_orders={summary.open_orders}",
            "",
            "[recent_orders]",
        ]
    )
    lines.extend(_format_rows(summary.recent_orders, ("symbol", "side", "qty", "filled_qty", "remaining_qty", "status", "broker_order_id")))
    lines.extend(["", "[recent_fills]"])
    lines.extend(_format_rows(summary.recent_fills, ("symbol", "side", "fill_qty", "fill_price", "filled_at")))
    lines.extend(["", "[recent_errors]"])
    lines.extend(_format_rows(summary.recent_errors, ("severity", "component", "event_type", "message", "occurred_at")))
    return "\n".join(lines)


def _count_positions(connection: sqlite3.Connection, statuses: tuple[str, ...]) -> int:
    placeholders = ", ".join("?" for _ in statuses)
    row = connection.execute(
        f"SELECT COUNT(*) AS cnt FROM positions WHERE status IN ({placeholders})",
        statuses,
    ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def _count_orders(connection: sqlite3.Connection, statuses: tuple[str, ...]) -> int:
    placeholders = ", ".join("?" for _ in statuses)
    row = connection.execute(
        f"SELECT COUNT(*) AS cnt FROM orders WHERE status IN ({placeholders})",
        statuses,
    ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def _fetch_rows(connection: sqlite3.Connection, query: str) -> list[dict[str, object]]:
    rows = connection.execute(query).fetchall()
    return [dict(row) for row in rows]


def _format_rows(rows: list[dict[str, object]], fields: tuple[str, ...]) -> list[str]:
    if not rows:
        return ["<none>"]
    formatted: list[str] = []
    for row in rows:
        parts = [f"{field}={row.get(field)}" for field in fields]
        formatted.append(" | ".join(parts))
    return formatted
