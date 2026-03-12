from __future__ import annotations

from auto_trading.common.enums import OrderStatus


TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.FAILED,
}


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL_ORDER_STATUSES
