from __future__ import annotations

from dataclasses import dataclass

from auto_trading.market_data.indicators import atr, average_volume, rate_of_change, rsi, simple_moving_average
from auto_trading.strategy.models import Bar, StrategyScore


@dataclass(slots=True)
class StrategyScorer:
    def score(self, bars: list[Bar]) -> StrategyScore:
        closes = [bar.close for bar in bars]
        volumes = [bar.volume for bar in bars]
        ma5 = simple_moving_average(closes[-5:]) if len(closes) >= 5 else 0.0
        ma20 = simple_moving_average(closes[-20:]) if len(closes) >= 20 else 0.0
        momentum_20 = rate_of_change(closes, 20)
        rsi_14 = rsi(closes, 14)
        atr_14 = atr(bars, 14)
        avg_vol_5 = average_volume(bars, 5)
        avg_vol_20 = average_volume(bars, 20)
        volume_ratio = 0.0 if avg_vol_20 == 0 else avg_vol_5 / avg_vol_20

        volume_score = 20 if volume_ratio >= 1.5 else 10 if volume_ratio >= 1.1 else 0
        momentum_score = 20 if momentum_20 >= 8 else 10 if momentum_20 >= 3 else 0
        ma_score = 0
        if closes[-1] > ma20:
            ma_score += 20
        if ma5 > ma20:
            ma_score += 20
        atr_score = 20 if 0.5 <= atr_14 <= 3.0 else 10 if 0 < atr_14 < 5.0 else 0
        rsi_score = 20 if 45 <= rsi_14 <= 65 else 10 if 35 <= rsi_14 <= 70 else 0
        total = min(volume_score + momentum_score + ma_score + atr_score + rsi_score, 100)

        return StrategyScore(
            symbol=bars[-1].symbol,
            score_total=total,
            price=closes[-1],
            volume_score=volume_score,
            momentum_score=momentum_score,
            ma_score=ma_score,
            atr_score=atr_score,
            rsi_score=rsi_score,
            ma5=ma5,
            ma20=ma20,
            rsi=rsi_14,
            atr=atr_14,
            momentum_20=momentum_20,
            volume_ratio=volume_ratio,
        )
