from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from auto_trading.app.dashboard import SEOUL_TZ, _format_exit_reason, _format_percent, _format_ratio, _format_signed_number, _load_symbol_name_map, _parse_datetime
from auto_trading.config.settings import load_settings


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'Invalid date: {value}. Use YYYY-MM-DD.') from exc


def _resolve_period(args: argparse.Namespace) -> tuple[date, date]:
    today = datetime.now(SEOUL_TZ).date()
    end_date = args.to_date or today
    if args.from_date is not None:
        start_date = args.from_date
    else:
        days = max(int(args.days or 7), 1)
        start_date = end_date - timedelta(days=days - 1)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _fetch_closed_trades(db_path: Path, master_path: Path | None, start_date: date, end_date: date) -> list[dict[str, object]]:
    if not db_path.exists():
        return []

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT tl.symbol, tl.qty, tl.entry_price, tl.exit_price, tl.gross_pnl, tl.net_pnl, tl.pnl_pct,
                   tl.entry_at, tl.exit_at, tl.exit_reason, o.updated_at AS exit_recorded_at
            FROM trade_logs tl
            LEFT JOIN orders o ON o.id = tl.exit_order_id
            WHERE tl.exit_at IS NOT NULL
            ORDER BY COALESCE(o.updated_at, tl.created_at) DESC, tl.id DESC
            """
        ).fetchall()
    finally:
        connection.close()

    name_map = _load_symbol_name_map(master_path)
    selected: list[dict[str, object]] = []
    for row in rows:
        exit_at = str(row['exit_at'] or '')
        exit_recorded_at = str(row['exit_recorded_at'] or '')
        exit_recorded_dt = _parse_datetime(exit_recorded_at)
        fallback_date = exit_recorded_dt.astimezone(SEOUL_TZ).date() if exit_recorded_dt is not None else None
        exit_dt = _parse_datetime(exit_at, fallback_date=fallback_date)
        if exit_dt is None:
            continue
        trade_date = exit_dt.astimezone(SEOUL_TZ).date()
        if trade_date < start_date or trade_date > end_date:
            continue
        symbol = str(row['symbol'] or '')
        selected.append(
            {
                'trade_date': trade_date.isoformat(),
                'symbol': symbol,
                'name': name_map.get(symbol, ''),
                'qty': int(row['qty'] or 0),
                'entry_price': float(row['entry_price'] or 0.0),
                'exit_price': float(row['exit_price'] or 0.0),
                'gross_pnl': float(row['gross_pnl'] or 0.0),
                'net_pnl': float(row['net_pnl'] or 0.0),
                'pnl_pct': float(row['pnl_pct'] or 0.0),
                'entry_at': str(row['entry_at'] or ''),
                'exit_at': exit_at,
                'exit_reason': str(row['exit_reason'] or ''),
            }
        )
    return selected


def _format_symbol(symbol: str, name: str) -> str:
    return f'{name}({symbol})' if name else symbol


def _render_report(start_date: date, end_date: date, trades: list[dict[str, object]]) -> str:
    realized_pnl = sum(float(item.get('net_pnl') or 0.0) for item in trades)
    closed_trade_count = len(trades)
    winning_trade_count = sum(1 for item in trades if float(item.get('net_pnl') or 0.0) > 0.0)
    win_rate = (winning_trade_count / closed_trade_count) if closed_trade_count else None
    average_pnl_pct = (
        sum(float(item.get('pnl_pct') or 0.0) for item in trades) / closed_trade_count
        if closed_trade_count else None
    )

    best_trade = max(trades, key=lambda item: float(item.get('net_pnl') or 0.0), default={})
    worst_trade = min(trades, key=lambda item: float(item.get('net_pnl') or 0.0), default={})

    daily_totals: dict[str, dict[str, object]] = {}
    symbol_totals: dict[str, dict[str, object]] = {}
    for trade in sorted(trades, key=lambda item: str(item.get('trade_date') or '')):
        trade_date = str(trade.get('trade_date') or '')
        net_pnl = float(trade.get('net_pnl') or 0.0)
        daily_entry = daily_totals.setdefault(trade_date, {'date': trade_date, 'net_pnl': 0.0, 'count': 0})
        daily_entry['net_pnl'] = float(daily_entry['net_pnl']) + net_pnl
        daily_entry['count'] = int(daily_entry['count']) + 1

        symbol = str(trade.get('symbol') or '')
        symbol_entry = symbol_totals.setdefault(
            symbol,
            {'symbol': symbol, 'name': str(trade.get('name') or ''), 'net_pnl': 0.0, 'count': 0},
        )
        symbol_entry['net_pnl'] = float(symbol_entry['net_pnl']) + net_pnl
        symbol_entry['count'] = int(symbol_entry['count']) + 1

    lines = [
        '[AUTO_TRADING] 실현손익 조회',
        f'기간: {start_date.isoformat()} ~ {end_date.isoformat()}',
        '',
        '[실현손익 요약]',
        f'총 실현손익: {_format_signed_number(realized_pnl)}원',
        f'청산 거래: {closed_trade_count}건',
        f'승률: {_format_ratio(win_rate)}',
        f'평균 수익률: {_format_percent(average_pnl_pct)}',
        '',
        '[일별 실현손익]',
    ]
    if daily_totals:
        for item in sorted(daily_totals.values(), key=lambda item: str(item['date'])):
            lines.append(f"{item['date']} | {_format_signed_number(item['net_pnl'])}원 | 청산 {item['count']}건")
    else:
        lines.append('없음')

    lines.extend(['', '[종목별 실현손익]'])
    if symbol_totals:
        for item in sorted(symbol_totals.values(), key=lambda item: (float(item['net_pnl']), item['symbol']), reverse=True):
            lines.append(
                f"{_format_symbol(str(item['symbol']), str(item['name']))} | {_format_signed_number(item['net_pnl'])}원 | 청산 {item['count']}건"
            )
    else:
        lines.append('없음')

    lines.extend(['', '[청산 내역]'])
    if trades:
        for trade in sorted(trades, key=lambda item: (str(item.get('trade_date') or ''), str(item.get('symbol') or '')), reverse=True):
            lines.append(
                f"- {trade['trade_date']} | {_format_symbol(str(trade['symbol']), str(trade['name']))} | 손익={_format_signed_number(trade['net_pnl'])}원 | 수익률={_format_percent(trade['pnl_pct'])} | 사유={_format_exit_reason(trade['exit_reason'])}"
            )
    else:
        lines.append('없음')

    lines.extend(['', '[최고/최저]'])
    if best_trade:
        lines.append(f"최고 수익: {_format_symbol(str(best_trade['symbol']), str(best_trade['name']))} {_format_signed_number(best_trade['net_pnl'])}원")
    else:
        lines.append('최고 수익: 없음')
    if worst_trade:
        lines.append(f"최대 손실: {_format_symbol(str(worst_trade['symbol']), str(worst_trade['name']))} {_format_signed_number(worst_trade['net_pnl'])}원")
    else:
        lines.append('최대 손실: 없음')
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description='Show realized PnL over a date range.')
    parser.add_argument('--from', dest='from_date', type=_parse_date, default=None, help='Start date (YYYY-MM-DD).')
    parser.add_argument('--to', dest='to_date', type=_parse_date, default=None, help='End date (YYYY-MM-DD). Defaults to today.')
    parser.add_argument('--days', type=int, default=7, help='Days to look back when --from is omitted. Defaults to 7.')
    args = parser.parse_args()

    start_date, end_date = _resolve_period(args)
    settings = load_settings()
    trades = _fetch_closed_trades(settings.db_path, settings.universe_master_path, start_date, end_date)
    print(_render_report(start_date, end_date, trades))


if __name__ == '__main__':
    main()
