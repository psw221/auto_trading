from __future__ import annotations

from dataclasses import dataclass, field

from auto_trading.common.time import utc_now


@dataclass(slots=True)
class Order:
    symbol: str
    side: str
    qty: int
    order_type: str
    id: int | None = None
    client_order_id: str = ""
    broker_order_id: str | None = None
    position_id: int | None = None
    intent: str = "ENTRY"
    price: float | None = None
    filled_qty: int = 0
    remaining_qty: int = 0
    status: str = "PENDING_CREATE"
    submitted_at: str | None = None
    last_broker_update_at: str | None = None
    failure_reason: str | None = None
    created_at: str = field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = field(default_factory=lambda: utc_now().isoformat())

    def __post_init__(self) -> None:
        if not self.client_order_id:
            self.client_order_id = f"{self.symbol}-{self.side}-{self.created_at}"
        if self.id is None and self.remaining_qty == 0:
            self.remaining_qty = self.qty
