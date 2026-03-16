from __future__ import annotations

from dataclasses import dataclass

from auto_trading.common.time import utc_now
from auto_trading.portfolio.models import Position


@dataclass(slots=True)
class PositionsRepository:
    db: object

    def upsert(self, position: Position) -> None:
        now = utc_now().isoformat()
        position.updated_at = now
        if position.id is None:
            position.created_at = position.created_at or now
            with self.db.transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO positions (
                        symbol,
                        name,
                        strategy_name,
                        status,
                        qty,
                        avg_entry_price,
                        current_price,
                        score_at_entry,
                        target_weight,
                        opened_at,
                        closed_at,
                        exit_reason,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position.symbol,
                        position.name,
                        position.strategy_name,
                        position.status,
                        position.qty,
                        position.avg_entry_price,
                        position.current_price,
                        position.score_at_entry,
                        position.target_weight,
                        position.opened_at,
                        position.closed_at,
                        position.exit_reason,
                        position.created_at,
                        position.updated_at,
                    ),
                )
            position.id = int(cursor.lastrowid)
            return

        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE positions
                SET symbol = ?,
                    name = ?,
                    strategy_name = ?,
                    status = ?,
                    qty = ?,
                    avg_entry_price = ?,
                    current_price = ?,
                    score_at_entry = ?,
                    target_weight = ?,
                    opened_at = ?,
                    closed_at = ?,
                    exit_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    position.symbol,
                    position.name,
                    position.strategy_name,
                    position.status,
                    position.qty,
                    position.avg_entry_price,
                    position.current_price,
                    position.score_at_entry,
                    position.target_weight,
                    position.opened_at,
                    position.closed_at,
                    position.exit_reason,
                    position.updated_at,
                    position.id,
                ),
            )

    def find_active(self) -> list[Position]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM positions
                WHERE status IN ('OPENING', 'OPEN', 'CLOSING')
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_all(self) -> list[Position]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM positions
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_by_statuses(self, statuses: list[str]) -> list[Position]:
        placeholders = ", ".join("?" for _ in statuses)
        with self.db.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM positions
                WHERE status IN ({placeholders})
                ORDER BY updated_at DESC
                """,
                statuses,
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_active_by_symbol(self, symbol: str) -> Position | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM positions
                WHERE symbol = ?
                  AND status IN ('OPENING', 'OPEN', 'CLOSING')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def find_all_by_symbol(self, symbol: str) -> list[Position]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM positions
                WHERE symbol = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (symbol,),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_by_symbol(self, symbol: str) -> Position | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM positions
                WHERE symbol = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def find_by_id(self, position_id: int) -> Position | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM positions
                WHERE id = ?
                """,
                (position_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def _to_model(self, row: object) -> Position:
        return Position(
            id=row["id"],
            symbol=row["symbol"],
            qty=row["qty"],
            name=row["name"] or "",
            strategy_name=row["strategy_name"],
            avg_entry_price=row["avg_entry_price"] or 0.0,
            current_price=row["current_price"],
            score_at_entry=row["score_at_entry"],
            target_weight=row["target_weight"],
            status=row["status"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            exit_reason=row["exit_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
