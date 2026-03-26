from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from auto_trading.config.settings import load_settings


SEOUL_TZ = timezone(timedelta(hours=9))


@dataclass(slots=True)
class ExitCase:
    symbol: str
    exit_at: datetime
    exit_reason: str
    exit_intent: str
    exit_score: int | None
    exit_price: float | None
    exit_ma5: float | None
    later_best_score: int | None
    later_best_time: datetime | None
    later_best_price: float | None
    later_best_ma5: float | None
    later_first_reentry_score: int | None
    later_first_reentry_time: datetime | None
    later_first_reentry_price: float | None
    later_first_reentry_ma5: float | None


def _parse_datetime(value: object) -> datetime | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_exit_cases(conn: sqlite3.Connection, *, days: int) -> list[ExitCase]:
    threshold = (datetime.now(timezone.utc) - timedelta(days=max(days, 1))).isoformat()
    rows = conn.execute(
        """
        SELECT
            tl.symbol,
            tl.exit_at,
            tl.exit_reason,
            COALESCE(o.intent, '') AS exit_intent
        FROM trade_logs tl
        LEFT JOIN orders o ON o.id = tl.exit_order_id
        WHERE COALESCE(tl.exit_at, '') >= ?
          AND (
              LOWER(COALESCE(tl.exit_reason, '')) = 'ma5_breakdown'
              OR UPPER(COALESCE(o.intent, '')) = 'MA5_BREAKDOWN'
          )
        ORDER BY tl.exit_at DESC
        """,
        (threshold,),
    ).fetchall()
    results: list[ExitCase] = []
    for row in rows:
        exit_at = _parse_datetime(row['exit_at'])
        if exit_at is None:
            continue
        results.append(
            ExitCase(
                symbol=str(row['symbol'] or '').strip(),
                exit_at=exit_at,
                exit_reason=str(row['exit_reason'] or '').strip(),
                exit_intent=str(row['exit_intent'] or '').strip(),
                exit_score=None,
                exit_price=None,
                exit_ma5=None,
                later_best_score=None,
                later_best_time=None,
                later_best_price=None,
                later_best_ma5=None,
                later_first_reentry_score=None,
                later_first_reentry_time=None,
                later_first_reentry_price=None,
                later_first_reentry_ma5=None,
            )
        )
    return results


def _enrich_case(conn: sqlite3.Connection, case: ExitCase, *, same_day_only: bool) -> ExitCase:
    exit_snapshot = conn.execute(
        """
        SELECT snapshot_time, score_total, price, ma5
        FROM strategy_snapshots
        WHERE symbol = ?
          AND snapshot_time <= ?
        ORDER BY snapshot_time DESC, id DESC
        LIMIT 1
        """,
        (case.symbol, case.exit_at.isoformat()),
    ).fetchone()
    if exit_snapshot is not None:
        case.exit_score = _to_int(exit_snapshot['score_total'])
        case.exit_price = _to_float(exit_snapshot['price'])
        case.exit_ma5 = _to_float(exit_snapshot['ma5'])

    lower_bound = case.exit_at.isoformat()
    params: list[object] = [case.symbol, lower_bound]
    same_day_sql = ''
    if same_day_only:
        exit_day_start = case.exit_at.astimezone(SEOUL_TZ).date().isoformat()
        exit_day_end = (case.exit_at.astimezone(SEOUL_TZ).date() + timedelta(days=1)).isoformat()
        same_day_sql = """
          AND date(datetime(snapshot_time, '+9 hours')) >= ?
          AND date(datetime(snapshot_time, '+9 hours')) < ?
        """
        params.extend([exit_day_start, exit_day_end])

    later_rows = conn.execute(
        f"""
        SELECT snapshot_time, score_total, price, ma5
        FROM strategy_snapshots
        WHERE symbol = ?
          AND snapshot_time > ?
          {same_day_sql}
        ORDER BY snapshot_time ASC, id ASC
        """,
        params,
    ).fetchall()
    for row in later_rows:
        score = _to_int(row['score_total'])
        price = _to_float(row['price'])
        ma5 = _to_float(row['ma5'])
        snapshot_time = _parse_datetime(row['snapshot_time'])
        if snapshot_time is None:
            continue
        if score is not None and (case.later_best_score is None or score > case.later_best_score):
            case.later_best_score = score
            case.later_best_time = snapshot_time
            case.later_best_price = price
            case.later_best_ma5 = ma5
        if case.later_first_reentry_time is None and price is not None and ma5 is not None and ma5 > 0 and price >= ma5:
            case.later_first_reentry_score = score
            case.later_first_reentry_time = snapshot_time
            case.later_first_reentry_price = price
            case.later_first_reentry_ma5 = ma5
    return case


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return '-'
    return value.astimezone(SEOUL_TZ).isoformat(timespec='seconds')


def _format_num(value: float | int | None) -> str:
    if value is None:
        return '-'
    if isinstance(value, int):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return f'{float(value):.2f}'


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Diagnose whether MA5-breakdown exits would have met a later score-improvement re-entry rule.'
    )
    parser.add_argument('--days', type=int, default=7, help='Look back this many days for MA5-breakdown exits.')
    parser.add_argument(
        '--score-improvement',
        type=int,
        default=5,
        help='Required score increase versus the exit-time score to count as a hypothetical re-entry.',
    )
    parser.add_argument(
        '--same-day-only',
        action='store_true',
        help='Only inspect later snapshots from the same Seoul trading day as the exit.',
    )
    parser.add_argument('--limit', type=int, default=20, help='Max exit cases to print.')
    args = parser.parse_args()

    settings = load_settings()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        raw_cases = _load_exit_cases(conn, days=args.days)
        cases = [_enrich_case(conn, case, same_day_only=args.same_day_only) for case in raw_cases]
    finally:
        conn.close()

    improved_hits = 0
    missing_exit_score = 0
    reentry_candidates = 0

    print(f'db_path={settings.db_path}')
    print(
        f'ma5_exit_count={len(cases)} days={max(args.days, 1)} '
        f'score_improvement={args.score_improvement} same_day_only={bool(args.same_day_only)}'
    )
    print('')

    for index, case in enumerate(cases[: max(args.limit, 1)], start=1):
        if case.exit_score is None:
            missing_exit_score += 1
        if case.later_first_reentry_time is not None:
            reentry_candidates += 1
            if case.exit_score is not None and case.later_first_reentry_score is not None:
                if case.later_first_reentry_score >= case.exit_score + args.score_improvement:
                    improved_hits += 1
        print(
            f'{index}. {case.symbol} '
            f'exit_at={_format_dt(case.exit_at)} '
            f'intent={case.exit_intent or "-"} '
            f'exit_reason={case.exit_reason or "-"}'
        )
        print(
            f'   exit_score={_format_num(case.exit_score)} '
            f'exit_price={_format_num(case.exit_price)} '
            f'exit_ma5={_format_num(case.exit_ma5)}'
        )
        print(
            f'   first_reentry_snapshot={_format_dt(case.later_first_reentry_time)} '
            f'score={_format_num(case.later_first_reentry_score)} '
            f'price={_format_num(case.later_first_reentry_price)} '
            f'ma5={_format_num(case.later_first_reentry_ma5)}'
        )
        print(
            f'   best_later_score={_format_num(case.later_best_score)} '
            f'at={_format_dt(case.later_best_time)} '
            f'price={_format_num(case.later_best_price)} '
            f'ma5={_format_num(case.later_best_ma5)}'
        )
        if case.exit_score is None or case.later_first_reentry_score is None:
            verdict = 'UNKNOWN'
        elif case.later_first_reentry_score >= case.exit_score + args.score_improvement:
            verdict = 'PASS'
        else:
            verdict = 'FAIL'
        print(f'   hypothetical_reentry={verdict}')
        print('')

    print('summary:')
    print(f'  missing_exit_score={missing_exit_score}')
    print(f'  first_reentry_candidates={reentry_candidates}')
    print(f'  improved_reentry_hits={improved_hits}')


if __name__ == '__main__':
    main()
