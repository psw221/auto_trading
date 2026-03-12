from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from auto_trading.common.holiday_generator import generate_holiday_csv

DEFAULT_OUTPUT_PATH = Path("data/krx_holidays.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate KRX holiday calendar CSV.")
    parser.add_argument("--year", type=int, default=datetime.now().year, help="Target year.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output CSV path.")
    parser.add_argument(
        "--service-key",
        default=os.getenv("AUTO_TRADING_HOLIDAY_API_SERVICE_KEY", ""),
        help="Data.go.kr service key for KASI holiday API.",
    )
    args = parser.parse_args()

    row_count = generate_holiday_csv(args.output, args.year, args.service_key)
    print(f"Wrote {row_count} rows to {args.output}")


if __name__ == "__main__":
    main()
