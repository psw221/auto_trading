from __future__ import annotations

from auto_trading.app.bootstrap import bootstrap


def main() -> None:
    container = bootstrap()
    result = container.portfolio_service.backfill_missing_trade_log_exits()
    backfilled = result.get('backfilled', [])
    skipped = result.get('skipped', [])
    print(f"backfilled_count={len(backfilled)}")
    for item in backfilled:
        print(f"backfilled symbol={item.get('symbol')} order_id={item.get('order_id')} exit_price={item.get('exit_price')}")
    print(f"skipped_count={len(skipped)}")
    for item in skipped:
        print(f"skipped symbol={item.get('symbol')} order_id={item.get('order_id')} reason={item.get('reason')}")


if __name__ == '__main__':
    main()
