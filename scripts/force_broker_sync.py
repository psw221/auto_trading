from __future__ import annotations

import argparse

from auto_trading.app.bootstrap import bootstrap


def main() -> None:
    parser = argparse.ArgumentParser(description='Force local positions to authoritative broker holdings.')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change without applying it.')
    parser.add_argument('--allow-empty', action='store_true', help='Apply even if broker holdings are empty.')
    parser.add_argument('--confirm-rounds', type=int, default=2, help='How many identical broker holdings reads are required before applying.')
    args = parser.parse_args()

    app = bootstrap()
    result = app.portfolio_service.force_sync_from_broker(
        dry_run=args.dry_run,
        allow_empty=args.allow_empty,
        confirm_rounds=args.confirm_rounds,
    )
    print(f"applied={result.get('applied', False)}")
    print(f"aborted_reason={result.get('aborted_reason', '') or '<none>'}")
    print(f"broker_symbols={','.join(result['broker_symbols']) or '<none>'}")
    print(f"closed_symbols={','.join(result['closed_symbols']) or '<none>'}")
    print(f"recovered_symbols={','.join(result['recovered_symbols']) or '<none>'}")
    print(f"created_symbols={','.join(result['created_symbols']) or '<none>'}")
    print(f"dry_run={bool(result.get('dry_run', False))}")
    print(f"confirm_rounds={int(result.get('confirm_rounds', args.confirm_rounds))}")


if __name__ == '__main__':
    main()
