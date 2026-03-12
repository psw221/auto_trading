from __future__ import annotations

from auto_trading.strategy.models import Bar


def simple_moving_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def rate_of_change(values: list[float], period: int) -> float:
    if len(values) <= period:
        return 0.0
    base = values[-period - 1]
    if base == 0:
        return 0.0
    return ((values[-1] - base) / base) * 100


def average_volume(bars: list[Bar], period: int) -> float:
    if len(bars) < period:
        return 0.0
    values = [bar.volume for bar in bars[-period:]]
    return simple_moving_average(values)


def average_turnover(bars: list[Bar], period: int) -> float:
    if len(bars) < period:
        return 0.0
    values = [bar.turnover for bar in bars[-period:]]
    return simple_moving_average(values)


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 0.0
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = simple_moving_average(gains)
    avg_loss = simple_moving_average(losses)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(bars: list[Bar], period: int = 14) -> float:
    if len(bars) <= period:
        return 0.0
    true_ranges: list[float] = []
    relevant = bars[-period - 1 :]
    for previous, current in zip(relevant[:-1], relevant[1:]):
        tr = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        true_ranges.append(tr)
    return simple_moving_average(true_ranges)
