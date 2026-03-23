from __future__ import annotations

from dataclasses import dataclass

from auto_trading.common.time import utc_now
from auto_trading.orders.models import Order
from auto_trading.portfolio.models import Position


@dataclass(slots=True)
class TradeLogsRepository:
    db: object

    def create_entry(self, position: Position, order: Order) -> int:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trade_logs (
                    position_id,
                    symbol,
                    strategy_name,
                    entry_order_id,
                    entry_price,
                    qty,
                    entry_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.id,
                    position.symbol,
                    position.strategy_name,
                    order.id,
                    position.avg_entry_price,
                    position.qty,
                    position.opened_at,
                    utc_now().isoformat(),
                ),
            )
        return int(cursor.lastrowid)

    def has_open_trade(self, position_id: int | None) -> bool:
        if position_id is None:
            return False
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM trade_logs
                WHERE position_id = ?
                  AND exit_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (position_id,),
            ).fetchone()
        return row is not None

    def close_trade(self, position: Position, order: Order, exit_price: float) -> None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, entry_price, qty
                FROM trade_logs
                WHERE position_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (position.id,),
            ).fetchone()
            if row is None:
                return
            entry_price = row["entry_price"] or 0.0
            qty = row["qty"] or position.qty
            gross_pnl = (exit_price - entry_price) * qty
            pnl_pct = 0.0 if entry_price == 0 else ((exit_price - entry_price) / entry_price) * 100
            connection.execute(
                """
                UPDATE trade_logs
                SET exit_order_id = ?,
                    exit_price = ?,
                    gross_pnl = ?,
                    net_pnl = ?,
                    pnl_pct = ?,
                    exit_at = ?,
                    exit_reason = ?
                WHERE id = ?
                """,
                (
                    order.id,
                    exit_price,
                    gross_pnl,
                    gross_pnl,
                    pnl_pct,
                    position.closed_at,
                    position.exit_reason,
                    row["id"],
                ),
            )
