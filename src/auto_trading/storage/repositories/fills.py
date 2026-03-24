from __future__ import annotations

from dataclasses import dataclass

from auto_trading.broker.dto import BrokerFillSnapshot
from auto_trading.common.time import utc_now


@dataclass(slots=True)
class FillsRepository:
    db: object

    def create(self, order_id: int, fill: BrokerFillSnapshot) -> int:
        existing_id = self.find_existing_id(order_id, fill)
        if existing_id is not None:
            return existing_id
        created_at = utc_now().isoformat()
        fill_amount = fill.fill_price * fill.fill_qty
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO fills (
                    order_id,
                    broker_fill_id,
                    symbol,
                    side,
                    fill_price,
                    fill_qty,
                    fill_amount,
                    filled_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    fill.order_no,
                    fill.symbol,
                    fill.side,
                    fill.fill_price,
                    fill.fill_qty,
                    fill_amount,
                    fill.filled_at,
                    created_at,
                ),
            )
        return int(cursor.lastrowid)

    def find_existing_id(self, order_id: int, fill: BrokerFillSnapshot) -> int | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM fills
                WHERE order_id = ?
                  AND broker_fill_id = ?
                  AND filled_at = ?
                  AND fill_qty = ?
                  AND fill_price = ?
                LIMIT 1
                """,
                (
                    order_id,
                    fill.order_no,
                    fill.filled_at,
                    fill.fill_qty,
                    fill.fill_price,
                ),
            ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def find_latest_for_order(self, order_id: int) -> dict[str, object] | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, broker_fill_id, symbol, side, fill_price, fill_qty, fill_amount, filled_at, created_at
                FROM fills
                WHERE order_id = ?
                ORDER BY filled_at DESC, id DESC
                LIMIT 1
                """,
                (order_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)
