from __future__ import annotations

from dataclasses import dataclass

from auto_trading.common.time import utc_now
from auto_trading.orders.models import Order

@dataclass(slots=True)
class OrdersRepository:
    db: object

    def create(self, order: Order) -> int:
        now = utc_now().isoformat()
        order.created_at = order.created_at or now
        order.updated_at = now
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO orders (
                    client_order_id,
                    broker_order_id,
                    position_id,
                    symbol,
                    side,
                    order_type,
                    intent,
                    price,
                    qty,
                    filled_qty,
                    remaining_qty,
                    status,
                    submitted_at,
                    last_broker_update_at,
                    failure_reason,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.client_order_id,
                    order.broker_order_id,
                    order.position_id,
                    order.symbol,
                    order.side,
                    order.order_type,
                    order.intent,
                    order.price,
                    order.qty,
                    order.filled_qty,
                    order.remaining_qty,
                    order.status,
                    order.submitted_at,
                    order.last_broker_update_at,
                    order.failure_reason,
                    order.created_at,
                    order.updated_at,
                ),
            )
        order.id = int(cursor.lastrowid)
        return order.id

    def update_status(self, order_id: int, status: str, **fields: object) -> None:
        allowed_fields = {
            "broker_order_id",
            "filled_qty",
            "remaining_qty",
            "submitted_at",
            "last_broker_update_at",
            "failure_reason",
        }
        assignments = ["status = ?", "updated_at = ?"]
        values: list[object] = [status, utc_now().isoformat()]
        for key, value in fields.items():
            if key not in allowed_fields:
                continue
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(order_id)
        with self.db.transaction() as connection:
            connection.execute(
                f"UPDATE orders SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def find_unknown_orders(self) -> list[Order]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE status = 'UNKNOWN'
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_by_statuses(self, statuses: list[str]) -> list[Order]:
        placeholders = ", ".join("?" for _ in statuses)
        with self.db.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM orders
                WHERE status IN ({placeholders})
                ORDER BY created_at ASC
                """,
                statuses,
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_open_for_symbol(self, symbol: str) -> list[Order]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE symbol = ?
                  AND status NOT IN ('FILLED', 'CANCELED', 'REJECTED', 'FAILED')
                ORDER BY created_at ASC
                """,
                (symbol,),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find_by_id(self, order_id: int) -> Order | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE id = ?
                """,
                (order_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def find_by_broker_order_id(self, broker_order_id: str) -> Order | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE broker_order_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (broker_order_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def find_latest_for_position(self, position_id: int) -> Order | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE position_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (position_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def _to_model(self, row: object) -> Order:
        return Order(
            id=row["id"],
            client_order_id=row["client_order_id"],
            broker_order_id=row["broker_order_id"],
            position_id=row["position_id"],
            symbol=row["symbol"],
            side=row["side"],
            qty=row["qty"],
            order_type=row["order_type"],
            intent=row["intent"],
            price=row["price"],
            filled_qty=row["filled_qty"],
            remaining_qty=row["remaining_qty"],
            status=row["status"],
            submitted_at=row["submitted_at"],
            last_broker_update_at=row["last_broker_update_at"],
            failure_reason=row["failure_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
