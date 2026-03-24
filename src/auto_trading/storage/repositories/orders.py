from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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

    def find_reconcilable_orders(self) -> list[Order]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE status IN ('UNKNOWN', 'SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED')
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

    def has_recent_rejected_exit(self, symbol: str, *, within_seconds: int) -> bool:
        if within_seconds <= 0:
            return False
        threshold = (utc_now() - timedelta(seconds=within_seconds)).isoformat()
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM orders
                WHERE symbol = ?
                  AND side = 'SELL'
                  AND status = 'REJECTED'
                  AND updated_at >= ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol, threshold),
            ).fetchone()
        return row is not None

    def has_filled_exit_intent_for_symbol_today(self, symbol: str, intent: str) -> bool:
        if not symbol or not intent:
            return False
        seoul = timezone(timedelta(hours=9))
        target_date = utc_now().astimezone(seoul).date()
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT updated_at, last_broker_update_at
                FROM orders
                WHERE symbol = ?
                  AND side = 'SELL'
                  AND status = 'FILLED'
                  AND intent = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 50
                """,
                (symbol, intent),
            ).fetchall()
        for row in rows:
            for key in ('last_broker_update_at', 'updated_at'):
                raw = str(row[key] or '').strip()
                if not raw:
                    continue
                try:
                    parsed = datetime.fromisoformat(raw)
                except ValueError:
                    continue
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed.astimezone(seoul).date() == target_date:
                    return True
        return False

    def find_stale_unknown_orders(self, *, older_than_seconds: int) -> list[Order]:
        if older_than_seconds <= 0:
            return []
        threshold = (utc_now() - timedelta(seconds=older_than_seconds)).isoformat()
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE status = 'UNKNOWN'
                  AND created_at <= ?
                ORDER BY created_at ASC
                """,
                (threshold,),
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

    def find_latest_unresolved_exit_for_position(self, position_id: int) -> Order | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE position_id = ?
                  AND side = 'SELL'
                  AND status = 'UNKNOWN'
                  AND broker_order_id IS NOT NULL
                  AND TRIM(COALESCE(broker_order_id, '')) <> ''
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (position_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def find_latest_entry_for_position(self, position_id: int) -> Order | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM orders
                WHERE position_id = ?
                  AND side = 'BUY'
                  AND status IN ('FILLED', 'UNKNOWN', 'SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (position_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_model(row)

    def find_filled_exits_missing_trade_logs(self) -> list[Order]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT o.*
                FROM orders o
                WHERE o.side = 'SELL'
                  AND o.status = 'FILLED'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM trade_logs tl
                      WHERE tl.exit_order_id = o.id
                  )
                ORDER BY o.updated_at ASC, o.id ASC
                """
            ).fetchall()
        return [self._to_model(row) for row in rows]

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

