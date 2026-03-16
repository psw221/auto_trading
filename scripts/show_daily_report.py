from __future__ import annotations

import argparse
from datetime import datetime

from auto_trading.app.dashboard import build_daily_report_summary, format_daily_report_summary
from auto_trading.config.settings import load_settings


def _parse_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'Invalid ISO datetime: {value}') from exc


def main() -> None:
    parser = argparse.ArgumentParser(description='Show auto trading daily report.')
    parser.add_argument('--at', type=_parse_at, default=None, help='ISO datetime override, e.g. 2026-03-16T16:30:00+09:00')
    args = parser.parse_args()

    settings = load_settings()
    summary = build_daily_report_summary(
        settings.db_path,
        settings.universe_master_path,
        now=args.at,
    )
    print(format_daily_report_summary(summary))


if __name__ == '__main__':
    main()
