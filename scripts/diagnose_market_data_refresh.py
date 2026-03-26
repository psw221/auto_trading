from __future__ import annotations

import argparse
import csv
from pathlib import Path

from auto_trading.broker.kis_client import KISClient
from auto_trading.config.settings import load_settings


def _load_current_universe(path: Path) -> list[str]:
    if not path.exists():
        return []
    symbols: list[str] = []
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = str(row.get('symbol') or '').strip()
            if symbol:
                symbols.append(symbol)
    return symbols


def main() -> None:
    parser = argparse.ArgumentParser(description='Diagnose broker REST market-data availability for current-universe symbols.')
    parser.add_argument('--symbol', action='append', dest='symbols', default=[], help='Specific symbol to diagnose. Repeatable.')
    parser.add_argument('--limit', type=int, default=20, help='Max current-universe symbols to inspect when --symbol is omitted.')
    args = parser.parse_args()

    settings = load_settings()
    current_universe_path = settings.universe_master_path.with_name('current_universe.csv')
    symbols = [str(symbol).strip() for symbol in args.symbols if str(symbol).strip()]
    if not symbols:
        symbols = _load_current_universe(current_universe_path)[: max(int(args.limit or 20), 1)]

    client = KISClient(settings)
    print(f'current_universe_path={current_universe_path}')
    print(f'symbol_count={len(symbols)}')
    for symbol in symbols:
        try:
            current = client.get_current_price(symbol)
            bars = client.get_daily_bars(symbol, lookback_days=30)
            price = float(current.get('price') or 0.0)
            turnover = float(current.get('turnover') or 0.0)
            valid_bars = [item for item in bars if float(item.get('close') or 0.0) > 0.0]
            latest_close = float(valid_bars[0].get('close') or 0.0) if valid_bars else 0.0
            status = 'OK'
            if price <= 0.0:
                status = 'BAD_PRICE'
            elif len(valid_bars) < 20:
                status = 'INSUFFICIENT_BARS'
            print(
                f'{symbol} status={status} price={price} turnover={turnover} bars={len(valid_bars)} latest_close={latest_close}'
            )
        except Exception as exc:
            print(f'{symbol} status=ERROR error={exc!r}')


if __name__ == '__main__':
    main()
