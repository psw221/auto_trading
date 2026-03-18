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
    tracked_positions: list[dict[str, object]]
    today_targets: list[dict[str, object]]
    latest_market_scan: dict[str, object]


@dataclass(slots=True)
class StrategyTargetsSummary:
    db_exists: bool
    target_date: str
    today_targets: list[dict[str, object]]


@dataclass(slots=True)
class DailyReportSummary:
    db_exists: bool
    report_date: str
    active_positions: int
    today_fill_count: int
    traded_symbols: list[str]
    tracked_positions: list[dict[str, object]]
    today_trades: list[dict[str, object]]
    missed_entries: list[dict[str, object]]
    closed_trades: list[dict[str, object]]
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    closed_trade_count: int
    winning_trade_count: int
    win_rate: float | None
    average_closed_pnl_pct: float | None
    best_trade: dict[str, object]
    worst_trade: dict[str, object]
    error_events: list[dict[str, object]]
    order_issue_count: int
    latest_market_scan: dict[str, object]


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
            tracked_positions=[],
            today_targets=[],
            latest_market_scan={},
        )

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        tracked_positions = _fetch_tracked_positions(connection)
        active_positions = len(tracked_positions)
        opening_positions = sum(1 for item in tracked_positions if item.get("status") == "OPENING")
        closing_positions = sum(1 for item in tracked_positions if item.get("status") == "CLOSING")
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
        tracked_positions=tracked_positions,
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


def build_daily_report_summary(
    db_path: Path,
    universe_master_path: Path | None = None,
    now: datetime | None = None,
) -> DailyReportSummary:
    report_date = _target_date(now).isoformat()
    if not db_path.exists():
        return DailyReportSummary(
            db_exists=False,
            report_date=report_date,
            active_positions=0,
            today_fill_count=0,
            traded_symbols=[],
            tracked_positions=[],
            today_trades=[],
            missed_entries=[],
            closed_trades=[],
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            total_pnl=0.0,
            closed_trade_count=0,
            winning_trade_count=0,
            win_rate=None,
            average_closed_pnl_pct=None,
            best_trade={},
            worst_trade={},
            error_events=[],
            order_issue_count=0,
            latest_market_scan={},
        )

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        tracked_positions = _fetch_tracked_positions(connection)
        active_positions = len(tracked_positions)
        today_trades = _fetch_today_fills(connection, universe_master_path=universe_master_path, now=now, limit=50)
        traded_symbols = sorted({str(item.get('symbol', '')) for item in today_trades if item.get('symbol')})
        missed_entries = _fetch_today_missed_entries(connection, universe_master_path=universe_master_path, now=now, limit=5)
        closed_trades = _fetch_today_closed_trades(connection, universe_master_path=universe_master_path, now=now, limit=20)
        realized_pnl = sum(float(item.get('net_pnl') or 0.0) for item in closed_trades)
        unrealized_pnl = sum(_calculate_position_pnl(position) for position in tracked_positions)
        total_pnl = realized_pnl + unrealized_pnl
        closed_trade_count = len(closed_trades)
        winning_trade_count = sum(1 for item in closed_trades if float(item.get('net_pnl') or 0.0) > 0)
        win_rate = (winning_trade_count / closed_trade_count) if closed_trade_count else None
        average_closed_pnl_pct = (
            sum(float(item.get('pnl_pct') or 0.0) for item in closed_trades) / closed_trade_count
            if closed_trade_count else None
        )
        ranked_closed_trades = sorted(closed_trades, key=lambda item: float(item.get('net_pnl') or 0.0), reverse=True)
        best_trade = ranked_closed_trades[0] if ranked_closed_trades else {}
        worst_trade = ranked_closed_trades[-1] if ranked_closed_trades else {}
        error_events = _fetch_today_error_events(connection, now=now, limit=10)
        order_issue_count = _count_today_order_issues(connection, now=now)
        latest_market_scan = _fetch_latest_market_scan(connection)
    finally:
        connection.close()

    return DailyReportSummary(
        db_exists=True,
        report_date=report_date,
        active_positions=active_positions,
        today_fill_count=len(today_trades),
        traded_symbols=traded_symbols,
        tracked_positions=tracked_positions,
        today_trades=today_trades,
        missed_entries=missed_entries,
        closed_trades=closed_trades,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_pnl=total_pnl,
        closed_trade_count=closed_trade_count,
        winning_trade_count=winning_trade_count,
        win_rate=win_rate,
        average_closed_pnl_pct=average_closed_pnl_pct,
        best_trade=best_trade,
        worst_trade=worst_trade,
        error_events=error_events,
        order_issue_count=order_issue_count,
        latest_market_scan=latest_market_scan,
    )


def format_daily_report_summary(summary: DailyReportSummary) -> str:
    lines = [
        '[AUTO_TRADING] 일일 리포트',
        f'기준일: {summary.report_date}',
        '',
        '[요약]',
        f'보유 종목: {summary.active_positions}개',
        f'당일 체결: {summary.today_fill_count}건',
        f'거래 종목: {", ".join(summary.traded_symbols) if summary.traded_symbols else "없음"}',
        f'주문 이상: {summary.order_issue_count}건',
        f'에러 이벤트: {len(summary.error_events)}건',
        '',
        '[성과]',
        f'실현손익: {_format_signed_number(summary.realized_pnl)}원',
        f'미실현손익: {_format_signed_number(summary.unrealized_pnl)}원',
        f'총손익: {_format_signed_number(summary.total_pnl)}원',
        f'청산 거래: {summary.closed_trade_count}건',
        f'승률: {_format_ratio(summary.win_rate)}',
        f'평균 수익률: {_format_percent(summary.average_closed_pnl_pct)}',
    ]
    if summary.latest_market_scan:
        lines.append(
            '최신 스캔: ' +
            f"universe={summary.latest_market_scan.get('universe_count', 0)} / " +
            f"scored={summary.latest_market_scan.get('scored_count', 0)} / " +
            f"qualified={summary.latest_market_scan.get('qualified_count', 0)}"
        )

    lines.extend(['', '[오늘 거래]'])
    if not summary.today_trades:
        lines.append('없음')
    else:
        for trade in summary.today_trades:
            name = trade.get('name') or ''
            symbol = trade.get('symbol') or ''
            display = f'{name}({symbol})' if name else symbol
            lines.append(
                f"- {display} {trade.get('side')} {trade.get('fill_qty')}주 @ {trade.get('fill_price')}원 ({trade.get('filled_at')})"
            )

    lines.extend(['', '[놓친 기회]'])
    if not summary.missed_entries:
        lines.append('없음')
    else:
        for entry in summary.missed_entries:
            name = entry.get('name') or ''
            symbol = entry.get('symbol') or ''
            display = f'{name}({symbol})' if name else symbol
            lines.append(
                f"- {display} | 점수={entry.get('score_total')} | 사유={entry.get('reason')}"
            )

    lines.extend(['', '[청산 내역]'])
    if not summary.closed_trades:
        lines.append('없음')
    else:
        for trade in summary.closed_trades:
            name = trade.get('name') or ''
            symbol = trade.get('symbol') or ''
            display = f'{name}({symbol})' if name else symbol
            lines.append(
                f"- {display} | 손익={_format_signed_number(trade.get('net_pnl'))}원 | 수익률={_format_percent(trade.get('pnl_pct'))} | 사유={_format_exit_reason(trade.get('exit_reason'))}"
            )

    lines.extend(['', '[최고/최저]'])
    if summary.best_trade:
        best_name = summary.best_trade.get('name') or ''
        best_symbol = summary.best_trade.get('symbol') or ''
        best_display = f'{best_name}({best_symbol})' if best_name else best_symbol
        lines.append(f"최고 수익: {best_display} {_format_signed_number(summary.best_trade.get('net_pnl'))}원")
    else:
        lines.append('최고 수익: 없음')
    if summary.worst_trade:
        worst_name = summary.worst_trade.get('name') or ''
        worst_symbol = summary.worst_trade.get('symbol') or ''
        worst_display = f'{worst_name}({worst_symbol})' if worst_name else worst_symbol
        lines.append(f"최대 손실: {worst_display} {_format_signed_number(summary.worst_trade.get('net_pnl'))}원")
    else:
        lines.append('최대 손실: 없음')

    lines.extend(['', '[보유 현황]'])
    if not summary.tracked_positions:
        lines.append('없음')
    else:
        for position in summary.tracked_positions:
            pnl = _calculate_position_pnl(position)
            pnl_pct = _calculate_position_pnl_pct(position)
            name = position.get('name') or ''
            symbol = position.get('symbol') or ''
            display = f'{name}({symbol})' if name else symbol
            lines.append(
                f"- {display} | 상태={position.get('status')} | 보유={position.get('qty')}주 | 평균단가={_format_number(position.get('avg_entry_price'))}원 | 현재가={_format_number(position.get('current_price'))}원 | 평가손익={_format_signed_number(pnl)}원 | 수익률={pnl_pct}"
            )

    lines.extend(['', '[이상/주의]'])
    if not summary.error_events:
        lines.append('없음')
    else:
        for event in summary.error_events:
            lines.append(
                f"- {event.get('component')} / {event.get('event_type')} / {event.get('message')}"
            )
    return "\n".join(lines)


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
        "[tracked_positions]",
    ])
    lines.extend(
        _format_rows(
            summary.tracked_positions,
            ("symbol", "name", "status", "qty", "avg_entry_price", "current_price", "updated_at"),
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


def _fetch_tracked_positions(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = _fetch_rows(
        connection,
        """
        SELECT symbol, name, status, qty, avg_entry_price, current_price, opened_at, updated_at
        FROM positions
        WHERE status IN ('OPENING', 'OPEN', 'CLOSING')
        ORDER BY updated_at DESC, id DESC
        LIMIT 50
        """,
    )
    return _dedupe_positions_by_symbol(rows)[:10]


def _dedupe_positions_by_symbol(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    selected: dict[str, dict[str, object]] = {}
    for row in rows:
        symbol = str(row.get('symbol', ''))
        if not symbol or symbol in selected:
            continue
        selected[symbol] = row
    return list(selected.values())


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


def _fetch_today_fills(
    connection: sqlite3.Connection,
    *,
    universe_master_path: Path | None,
    now: datetime | None,
    limit: int,
) -> list[dict[str, object]]:
    rows = _fetch_rows(
        connection,
        """
        SELECT symbol, side, fill_qty, fill_price, filled_at, created_at
        FROM fills
        ORDER BY created_at DESC, id DESC
        LIMIT 200
        """,
    )
    name_map = _load_symbol_name_map(universe_master_path)
    target_date = _target_date(now)
    selected: list[dict[str, object]] = []
    for row in rows:
        filled_at = str(row.get('filled_at', ''))
        created_at = str(row.get('created_at', ''))
        created_dt = _parse_datetime(created_at)
        fallback_date = created_dt.astimezone(SEOUL_TZ).date() if created_dt is not None else None
        filled_dt = _parse_datetime(filled_at, fallback_date=fallback_date)
        if filled_dt is None or filled_dt.astimezone(SEOUL_TZ).date() != target_date:
            continue
        selected.append(
            {
                'symbol': str(row.get('symbol', '')),
                'name': name_map.get(str(row.get('symbol', '')), ''),
                'side': str(row.get('side', '')),
                'fill_qty': int(row.get('fill_qty') or 0),
                'fill_price': _format_number(row.get('fill_price')),
                'filled_at': filled_at,
            }
        )
    return selected[:limit]

def _fetch_today_missed_entries(
    connection: sqlite3.Connection,
    *,
    universe_master_path: Path | None,
    now: datetime | None,
    limit: int,
) -> list[dict[str, object]]:
    targets = _fetch_today_targets(
        connection,
        universe_master_path=universe_master_path,
        now=now,
        limit=100,
    )
    if not targets:
        return []

    target_by_symbol = {str(item.get('symbol', '')): item for item in targets if item.get('symbol')}
    rows = _fetch_rows(
        connection,
        """
        SELECT event_type, message, payload_json, occurred_at
        FROM system_events
        WHERE event_type IN ('entry_skipped', 'order_blocked', 'duplicate_position', 'order_rejected', 'order_unknown', 'unknown_order_unresolved')
        ORDER BY occurred_at DESC, id DESC
        LIMIT 200
        """,
    )
    target_date = _target_date(now)
    selected_by_symbol: dict[str, dict[str, object]] = {}
    for row in rows:
        occurred_dt = _parse_datetime(str(row.get('occurred_at', '')))
        if occurred_dt is None or occurred_dt.astimezone(SEOUL_TZ).date() != target_date:
            continue
        payload = _parse_metadata(row.get('payload_json'))
        symbol = _resolve_event_symbol(connection, payload)
        if not symbol or symbol not in target_by_symbol:
            continue
        reason_code = _map_missed_entry_reason(str(row.get('event_type', '')), payload)
        if not reason_code:
            continue
        existing = selected_by_symbol.get(symbol)
        priority = _missed_entry_priority(reason_code)
        if existing is not None and priority >= int(existing.get('priority', 999)):
            continue
        target = target_by_symbol[symbol]
        selected_by_symbol[symbol] = {
            'symbol': symbol,
            'name': target.get('name', ''),
            'score_total': int(target.get('score_total') or 0),
            'reason_code': reason_code,
            'reason': _format_missed_entry_reason(reason_code, str(row.get('message', ''))),
            'priority': priority,
        }
    missed_entries = list(selected_by_symbol.values())
    missed_entries.sort(key=lambda item: (int(item.get('priority', 999)), -int(item.get('score_total', 0)), str(item.get('symbol', ''))))
    for item in missed_entries:
        item.pop('priority', None)
    return missed_entries[:limit]


def _resolve_event_symbol(connection: sqlite3.Connection, payload: dict[str, object]) -> str:
    symbol = str(payload.get('symbol', '')).strip()
    if symbol:
        return symbol
    order_id = payload.get('order_id')
    try:
        order_id_value = int(order_id)
    except (TypeError, ValueError):
        return ''
    row = connection.execute(
        "SELECT symbol FROM orders WHERE id = ? LIMIT 1",
        (order_id_value,),
    ).fetchone()
    if row is None:
        return ''
    return str(row['symbol'] or '').strip()


def _map_missed_entry_reason(event_type: str, payload: dict[str, object]) -> str:
    normalized = event_type.strip().lower()
    if normalized == 'entry_skipped':
        reason = str(payload.get('reason', '')).strip().lower()
        if reason == 'max_positions':
            return 'max_positions'
        return ''
    if normalized == 'order_blocked':
        return 'failsafe_blocked'
    if normalized == 'duplicate_position':
        return 'already_holding'
    if normalized == 'order_rejected':
        return 'order_rejected'
    if normalized in {'order_unknown', 'unknown_order_unresolved'}:
        return 'order_unknown'
    return ''


def _missed_entry_priority(reason_code: str) -> int:
    priority = {
        'order_rejected': 1,
        'order_unknown': 2,
        'failsafe_blocked': 3,
        'already_holding': 4,
        'max_positions': 5,
    }
    return priority.get(reason_code, 999)


def _format_missed_entry_reason(reason_code: str, detail: str) -> str:
    labels = {
        'max_positions': '보유 종목 수 한도 도달',
        'failsafe_blocked': 'Fail-safe 차단 상태',
        'already_holding': '이미 보유 중인 종목',
        'order_rejected': '주문 거절',
        'order_unknown': '주문 상태 미확정',
    }
    label = labels.get(reason_code, reason_code)
    detail_text = detail.strip()
    if reason_code in {'order_rejected', 'order_unknown'} and detail_text:
        return f'{label} - {detail_text}'
    return label


def _fetch_today_closed_trades(
    connection: sqlite3.Connection,
    *,
    universe_master_path: Path | None,
    now: datetime | None,
    limit: int,
) -> list[dict[str, object]]:
    rows = _fetch_rows(
        connection,
        """
        SELECT tl.symbol, tl.qty, tl.entry_price, tl.exit_price, tl.gross_pnl, tl.net_pnl, tl.pnl_pct, tl.entry_at, tl.exit_at, tl.exit_reason,
               o.updated_at AS exit_recorded_at
        FROM trade_logs tl
        LEFT JOIN orders o ON o.id = tl.exit_order_id
        WHERE tl.exit_at IS NOT NULL
        ORDER BY COALESCE(o.updated_at, tl.created_at) DESC, tl.id DESC
        LIMIT 100
        """,
    )
    name_map = _load_symbol_name_map(universe_master_path)
    target_date = _target_date(now)
    selected: list[dict[str, object]] = []
    for row in rows:
        exit_at = str(row.get('exit_at', ''))
        exit_recorded_at = str(row.get('exit_recorded_at', ''))
        exit_recorded_dt = _parse_datetime(exit_recorded_at)
        fallback_date = exit_recorded_dt.astimezone(SEOUL_TZ).date() if exit_recorded_dt is not None else None
        exit_dt = _parse_datetime(exit_at, fallback_date=fallback_date)
        if exit_dt is None or exit_dt.astimezone(SEOUL_TZ).date() != target_date:
            continue
        selected.append(
            {
                'symbol': str(row.get('symbol', '')),
                'name': name_map.get(str(row.get('symbol', '')), ''),
                'qty': int(row.get('qty') or 0),
                'entry_price': float(row.get('entry_price') or 0.0),
                'exit_price': float(row.get('exit_price') or 0.0),
                'gross_pnl': float(row.get('gross_pnl') or 0.0),
                'net_pnl': float(row.get('net_pnl') or 0.0),
                'pnl_pct': float(row.get('pnl_pct') or 0.0),
                'entry_at': str(row.get('entry_at', '')),
                'exit_at': exit_at,
                'exit_reason': str(row.get('exit_reason', '')),
            }
        )
    return selected[:limit]

def _fetch_today_error_events(
    connection: sqlite3.Connection,
    *,
    now: datetime | None,
    limit: int,
) -> list[dict[str, object]]:
    rows = _fetch_rows(
        connection,
        """
        SELECT severity, component, event_type, message, occurred_at
        FROM system_events
        WHERE severity IN ('ERROR', 'CRITICAL')
        ORDER BY occurred_at DESC, id DESC
        LIMIT 50
        """,
    )
    target_date = _target_date(now)
    selected: list[dict[str, object]] = []
    for row in rows:
        occurred_at = str(row.get('occurred_at', ''))
        occurred_dt = _parse_datetime(occurred_at)
        if occurred_dt is None or occurred_dt.astimezone(SEOUL_TZ).date() != target_date:
            continue
        selected.append(row)
    return selected[:limit]


def _count_today_order_issues(connection: sqlite3.Connection, *, now: datetime | None) -> int:
    rows = _fetch_rows(
        connection,
        """
        SELECT event_type, occurred_at
        FROM system_events
        WHERE event_type IN ('order_unknown', 'order_rejected', 'unknown_order_unresolved', 'broker_exception', 'reconcile_failed')
        ORDER BY occurred_at DESC, id DESC
        LIMIT 100
        """,
    )
    target_date = _target_date(now)
    count = 0
    for row in rows:
        occurred_at = str(row.get('occurred_at', ''))
        occurred_dt = _parse_datetime(occurred_at)
        if occurred_dt is None or occurred_dt.astimezone(SEOUL_TZ).date() != target_date:
            continue
        count += 1
    return count


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


def _calculate_position_pnl(position: dict[str, object]) -> float:
    try:
        qty = float(position.get('qty') or 0)
        avg_entry_price = float(position.get('avg_entry_price') or 0)
        current_price = float(position.get('current_price') or 0)
    except (TypeError, ValueError):
        return 0.0
    return (current_price - avg_entry_price) * qty


def _calculate_position_pnl_pct(position: dict[str, object]) -> str:
    try:
        avg_entry_price = float(position.get('avg_entry_price') or 0)
        current_price = float(position.get('current_price') or 0)
        if avg_entry_price <= 0:
            return '-'
        pnl_pct = ((current_price / avg_entry_price) - 1.0) * 100.0
        return f'{pnl_pct:+.2f}%'
    except (TypeError, ValueError):
        return '-'


def _format_number(value: object) -> str:
    try:
        return f'{float(value):,.0f}'
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or '-'


def _format_percent(value: object) -> str:
    try:
        return f'{float(value):+.2f}%'
    except (TypeError, ValueError):
        return '-'


def _format_ratio(value: float | None) -> str:
    if value is None:
        return '-'
    return f'{value * 100.0:.1f}%'


def _format_exit_reason(value: object) -> str:
    reason = str(value or '').strip().upper()
    mapping = {
        'TAKEPROFIT': '익절',
        'TAKE_PROFIT': '익절',
        'STOPLOSS': '손절',
        'STOP_LOSS': '손절',
        'TIMEEXIT': '보유 기간 종료',
        'TIME_EXIT': '보유 기간 종료',
        'MA5BREAKDOWN': '5일선 이탈',
        'MA5_BREAKDOWN': '5일선 이탈',
        'EXIT': '일반 청산',
    }
    return mapping.get(reason, str(value or '').strip() or '-')


def _format_signed_number(value: object) -> str:
    try:
        number = float(value)
        return f'{number:+,.0f}'
    except (TypeError, ValueError):
        return str(value).strip() or '-'


def _parse_metadata(value: object) -> dict[str, object]:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return {}


def _parse_datetime(value: str, *, fallback_date: object | None = None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        text = str(value).strip()
        if len(text) == 6 and text.isdigit() and fallback_date is not None:
            try:
                return datetime(
                    fallback_date.year,
                    fallback_date.month,
                    fallback_date.day,
                    int(text[0:2]),
                    int(text[2:4]),
                    int(text[4:6]),
                    tzinfo=SEOUL_TZ,
                )
            except (AttributeError, TypeError, ValueError):
                return None
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







