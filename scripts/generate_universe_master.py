from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from auto_trading.universe.master_generator import generate_master_csv

DEFAULT_OUTPUT_PATH = Path("data/universe_master.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate universe master CSV for auto trading.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output CSV path.")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Additional local CSV/TXT file path or HTTP(S) URL. Can be provided multiple times.",
    )
    parser.add_argument(
        "--skip-official",
        action="store_true",
        help="Skip official KIS GitHub stocks_info discovery and use only explicit --source inputs.",
    )
    args = parser.parse_args()

    row_count = generate_master_csv(
        output=args.output,
        sources=args.source,
        include_official=not args.skip_official,
    )
    print(f"Wrote {row_count} rows to {args.output}")


if __name__ == "__main__":
    main()
