from __future__ import annotations

from auto_trading.app.bootstrap import bootstrap


def main() -> None:
    container = bootstrap()
    result = container.portfolio_service.reconcile_eod_daily_fills()
    print(f"report_date={result.get('report_date')}")
    print(f"daily_fill_count={result.get('daily_fill_count')}")
    print(f"fills_backfilled_count={result.get('fills_backfilled_count')}")
    print(f"matched_order_count={result.get('matched_order_count')}")
    print(f"reconciled_order_count={result.get('reconciled_order_count')}")
    print(f"reconciled_position_count={result.get('reconciled_position_count')}")
    print(f"trade_logs_backfilled_count={result.get('trade_logs_backfilled_count')}")
    print(f"unmatched_fill_count={result.get('unmatched_fill_count')}")
    for item in result.get('trade_logs_backfilled', []):
        print(
            f"trade_log_backfilled order_id={item.get('order_id')} symbol={item.get('symbol')} "
            f"exit_price={item.get('exit_price')} source={item.get('source')}"
        )
    for item in result.get('trade_logs_skipped', []):
        print(
            f"trade_log_skipped order_id={item.get('order_id')} symbol={item.get('symbol')} "
            f"reason={item.get('reason')}"
        )
    for item in result.get('unmatched_fills', []):
        print(
            f"unmatched_fill broker_order_id={item.get('broker_order_id')} symbol={item.get('symbol')} "
            f"side={item.get('side')} fill_qty={item.get('fill_qty')} fill_price={item.get('fill_price')}"
        )


if __name__ == '__main__':
    main()
