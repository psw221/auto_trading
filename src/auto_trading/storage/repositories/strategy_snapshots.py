from __future__ import annotations

import json
from dataclasses import dataclass

from auto_trading.common.time import utc_now
from auto_trading.strategy.models import StrategyScore


@dataclass(slots=True)
class StrategySnapshotsRepository:
    db: object

    def create(self, score: StrategyScore) -> int:
        created_at = utc_now().isoformat()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO strategy_snapshots (
                    symbol,
                    snapshot_time,
                    score_total,
                    volume_score,
                    momentum_score,
                    ma_score,
                    atr_score,
                    rsi_score,
                    price,
                    ma5,
                    ma20,
                    rsi,
                    atr,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    score.symbol,
                    created_at,
                    score.score_total,
                    score.volume_score,
                    score.momentum_score,
                    score.ma_score,
                    score.atr_score,
                    score.rsi_score,
                    score.price,
                    score.ma5,
                    score.ma20,
                    score.rsi,
                    score.atr,
                    json.dumps(
                        {
                            "momentum_20": score.momentum_20,
                            "volume_ratio": score.volume_ratio,
                        },
                        ensure_ascii=True,
                    ),
                    created_at,
                ),
            )
        return int(cursor.lastrowid)
