from __future__ import annotations

from dataclasses import dataclass, field

from auto_trading.common.time import utc_now


@dataclass(slots=True)
class Position:
    symbol: str
    qty: int
    id: int | None = None
    name: str = ""
    strategy_name: str = "swing"
    avg_entry_price: float = 0.0
    current_price: float | None = None
    score_at_entry: int | None = None
    target_weight: float | None = None
    status: str = "READY"
    opened_at: str | None = None
    closed_at: str | None = None
    exit_reason: str | None = None
    created_at: str = field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = field(default_factory=lambda: utc_now().isoformat())


@dataclass(slots=True)
class PortfolioSnapshot:
    cash: float = 0.0
    total_asset: float = 0.0
    open_positions: list[Position] = field(default_factory=list)
