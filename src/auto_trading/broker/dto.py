from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BrokerOrderRequest:
    symbol: str
    side: str
    qty: int
    order_type: str
    price: float | None = None
    tr_id: str | None = None
    payload: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class BrokerReviseCancelRequest:
    orig_odno: str
    symbol: str
    qty: int
    mode: str
    price: float | None = None


@dataclass(slots=True)
class BrokerOrderResponse:
    order_no: str | None
    accepted: bool
    rt_cd: str = "0"
    msg_cd: str = ""
    msg: str = ""
    output: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class BrokerOrderSnapshot:
    order_no: str
    symbol: str
    status: str
    filled_qty: int
    remaining_qty: int


@dataclass(slots=True)
class BrokerFillSnapshot:
    order_no: str
    symbol: str
    side: str
    fill_qty: int
    fill_price: float
    filled_at: str


@dataclass(slots=True)
class BrokerBalance:
    cash: float
    total_asset: float


@dataclass(slots=True)
class BrokerPositionSnapshot:
    symbol: str
    qty: int
    avg_price: float
    current_price: float
    name: str = ""


@dataclass(slots=True)
class BrokerRealtimeEvent:
    event_type: str
    symbol: str | None = None
    payload: dict[str, str] = field(default_factory=dict)
