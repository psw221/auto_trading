from __future__ import annotations

import unittest

from auto_trading.strategy.models import Bar
from auto_trading.strategy.scorer import StrategyScorer


class StrategyScorerTest(unittest.TestCase):
    def test_score_uses_prd_indicators(self) -> None:
        bars: list[Bar] = []
        price = 100.0
        for index in range(25):
            price += 1.5
            bars.append(
                Bar(
                    symbol="005930",
                    open=price - 0.5,
                    high=price + 1.0,
                    low=price - 1.0,
                    close=price,
                    volume=1000 + (index * 100),
                    turnover=(1000 + (index * 100)) * price,
                )
            )

        score = StrategyScorer().score(bars)
        self.assertGreaterEqual(score.score_total, 70)
        self.assertGreater(score.ma5, score.ma20)
        self.assertGreater(score.volume_ratio, 1.0)
        self.assertGreater(score.momentum_20, 0.0)
        self.assertGreater(score.atr, 0.0)


if __name__ == "__main__":
    unittest.main()
