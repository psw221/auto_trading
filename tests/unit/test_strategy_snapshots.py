from __future__ import annotations

import unittest
from pathlib import Path

from auto_trading.storage.db import Database
from auto_trading.storage.repositories.strategy_snapshots import StrategySnapshotsRepository
from auto_trading.strategy.models import StrategyScore


class StrategySnapshotsRepositoryTest(unittest.TestCase):
    def test_create_persists_component_scores(self) -> None:
        db_path = Path("data/test_strategy_snapshots.db")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        db.initialize()
        repository = StrategySnapshotsRepository(db)
        snapshot_id = repository.create(
            StrategyScore(
                symbol="005930",
                score_total=80,
                price=70000.0,
                volume_score=20,
                momentum_score=20,
                ma_score=20,
                atr_score=10,
                rsi_score=10,
                ma5=70500.0,
                ma20=69000.0,
                rsi=55.0,
                atr=1.5,
                momentum_20=8.5,
                volume_ratio=1.8,
            )
        )
        self.assertGreater(snapshot_id, 0)
        with db.transaction() as connection:
            row = connection.execute(
                "SELECT score_total, volume_score, momentum_score, metadata_json FROM strategy_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        self.assertEqual(80, row["score_total"])
        self.assertEqual(20, row["volume_score"])
        self.assertEqual(20, row["momentum_score"])
        self.assertIn("volume_ratio", row["metadata_json"])


if __name__ == "__main__":
    unittest.main()
