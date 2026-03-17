from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Bar:
    symbol: str
    close: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    turnover: float = 0.0


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    price: float
    volume: float = 0.0
    turnover: float = 0.0
    ma5: float = 0.0
    ma20: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    momentum_20: float = 0.0
    volume_ratio: float = 0.0


@dataclass(slots=True)
class StrategyScore:
    symbol: str
    score_total: int
    price: float
    volume_score: int = 0
    momentum_score: int = 0
    ma_score: int = 0
    atr_score: int = 0
    rsi_score: int = 0
    ma5: float = 0.0
    ma20: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    momentum_20: float = 0.0
    volume_ratio: float = 0.0


@dataclass(slots=True)
class EntrySignal:
    symbol: str
    score_total: int
    price: float


@dataclass(slots=True)
class ExitSignal:
    symbol: str
    reason: str
    order_type: str
    price: float | None = None


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str


@dataclass(slots=True)
class OrderSizing:
    qty: int
    order_type: str
    price: float | None = None
