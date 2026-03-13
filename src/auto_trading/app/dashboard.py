from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEOUL_TZ = timezone(timedelta(hours=9))


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
    today_targets: list[dict[str, object]]
    latest_market_scan: dict[str, object]


@dataclass(slots=True)
class StrategyTargetsSummary:
    db_exists: bool
    target_date: str
    today_targets: list[dict[str, object]]


def build_dashboard_summary(
    db_path: Path,
    universe_master_path: Path | None = None,
    now: datetime | None = None,
) -> DashboardSummary:
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
            today_targets=[],
            latest_market_scan={},
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
        today_targets = _fetch_today_targets(
            connection,
            universe_master_path=universe_master_path,
            now=now,
            limit=10,
        )
        latest_market_scan = _fetch_latest_market_scan(connection)
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
        today_targets=today_targets,
        latest_market_scan=latest_market_scan,
    )


def build_strategy_targets_summary(
    db_path: Path,
    universe_master_path: Path | None = None,
    now: datetime | None = None,
    limit: int = 20,
) -> StrategyTargetsSummary:
    target_date = _target_date(now).isoformat()
    if not db_path.exists():
        return StrategyTargetsSummary(db_exists=False, target_date=target_date, today_targets=[])

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        today_targets = _fetch_today_targets(
            connection,
            universe_master_path=universe_master_path,
            now=now,
            limit=limit,
        )
    finally:
        connection.close()
    return StrategyTargetsSummary(db_exists=True, target_date=target_date, today_targets=today_targets)


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
            "[latest_market_scan]",
        ]
    )
    lines.extend(
        _format_rows(
            [summary.latest_market_scan] if summary.latest_market_scan else [],
            ("snapshot_time", "universe_count", "scored_count", "qualified_count", "top_candidate_count"),
        )
    )
    lines.extend([
        "",
        "[today_targets]",
    ])
    lines.extend(
        _format_rows(
            summary.today_targets,
            ("symbol", "name", "score_total", "price", "ma5", "ma20", "rsi", "atr", "snapshot_time"),
        )
    )
    lines.extend(["", "[recent_orders]"])
    lines.extend(_format_rows(summary.recent_orders, ("symbol", "side", "qty", "filled_qty", "remaining_qty", "status", "broker_order_id")))
    lines.extend(["", "[recent_fills]"])
    lines.extend(_format_rows(summary.recent_fills, ("symbol", "side", "fill_qty", "fill_price", "filled_at")))
    lines.extend(["", "[recent_errors]"])
    lines.extend(_format_rows(summary.recent_errors, ("severity", "component", "event_type", "message", "occurred_at")))
    return "\n".join(lines)


def format_strategy_targets_summary(summary: StrategyTargetsSummary, db_path: Path) -> str:
    lines = [f"db_path={db_path}", f"target_date={summary.target_date}"]
    if not summary.db_exists:
        lines.append("db_exists=False")
        return "\n".join(lines)
    lines.extend(["db_exists=True", "[today_targets]"])
    lines.extend(
        _format_rows(
            summary.today_targets,
            ("symbol", "name", "score_total", "price", "ma5", "ma20", "rsi", "atr", "snapshot_time"),
        )
    )
    return "\n".join(lines)


def _target_date(now: datetime | None) -> datetime.date:
    current = now.astimezone(SEOUL_TZ) if now is not None else datetime.now(SEOUL_TZ)
    return current.date()


def _fetch_latest_market_scan(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT payload_json
        FROM system_events
        WHERE event_type = 'market_scan_summary'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {}
    return _parse_metadata(row['payload_json'])


def _fetch_today_targets(
    connection: sqlite3.Connection,
    *,
    universe_master_path: Path | None,
    now: datetime | None,
    limit: int,
) -> list[dict[str, object]]:
    rows = _fetch_rows(
        connection,
        """
        SELECT symbol, snapshot_time, score_total, price, ma5, ma20, rsi, atr, metadata_json
        FROM strategy_snapshots
        ORDER BY snapshot_time DESC, id DESC
        LIMIT 500
        """,
    )
    name_map = _load_symbol_name_map(universe_master_path)
    target_date = _target_date(now)
    selected_by_symbol: dict[str, dict[str, object]] = {}
    for row in rows:
        snapshot_time = str(row.get("snapshot_time", ""))
        snapshot_dt = _parse_datetime(snapshot_time)
        if snapshot_dt is None or snapshot_dt.astimezone(SEOUL_TZ).date() != target_date:
            continue
        symbol = str(row.get("symbol", ""))
        if not symbol or symbol in selected_by_symbol:
            continue
        metadata = _parse_metadata(row.get("metadata_json"))
        selected_by_symbol[symbol] = {
            "symbol": symbol,
            "name": name_map.get(symbol, ""),
            "score_total": row.get("score_total"),
            "price": row.get("price"),
            "ma5": row.get("ma5"),
            "ma20": row.get("ma20"),
            "rsi": row.get("rsi"),
            "atr": row.get("atr"),
            "momentum_20": metadata.get("momentum_20"),
            "volume_ratio": metadata.get("volume_ratio"),
            "snapshot_time": snapshot_time,
        }
    targets = list(selected_by_symbol.values())
    targets.sort(key=lambda item: (int(item.get("score_total") or 0), str(item.get("snapshot_time") or "")), reverse=True)
    return targets[:limit]


def _load_symbol_name_map(universe_master_path: Path | None) -> dict[str, str]:
    if universe_master_path is None or not universe_master_path.exists():
        return {}
    result: dict[str, str] = {}
    try:
        with universe_master_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                symbol = str(row.get("symbol", "")).strip()
                name = str(row.get("name", "")).strip()
                if symbol and name:
                    result[symbol] = name
    except OSError:
        return {}
    return result


def _parse_metadata(value: object) -> dict[str, object]:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return {}


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


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
