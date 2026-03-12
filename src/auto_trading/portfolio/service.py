from __future__ import annotations

from dataclasses import dataclass

from auto_trading.broker.dto import BrokerFillSnapshot, BrokerPositionSnapshot
from auto_trading.common.time import utc_now
from auto_trading.portfolio.models import PortfolioSnapshot, Position


@dataclass(slots=True)
class PortfolioService:
    positions_repository: object
    orders_repository: object
    fills_repository: object
    trade_logs_repository: object
    kis_client: object
    system_events_repository: object | None = None

    def sync_from_broker(self) -> None:
        broker_positions = {item.symbol: item for item in self.kis_client.get_positions()}
        local_positions = self.positions_repository.find_all()

        for local_position in local_positions:
            broker_position = broker_positions.get(local_position.symbol)
            if broker_position is None:
                if local_position.status in {"OPEN", "OPENING", "CLOSING"} and local_position.qty > 0:
                    local_position.status = "ERROR"
                    local_position.exit_reason = "broker_position_missing"
                    self.positions_repository.upsert(local_position)
                    self._log_sync_event(
                        event_type="position_mismatch",
                        severity="WARN",
                        message="Local position not found in broker holdings during sync.",
                        payload={"symbol": local_position.symbol, "position_id": local_position.id},
                    )
                continue
            self._merge_broker_position(local_position, broker_position)

        existing_symbols = {item.symbol for item in local_positions}
        for symbol, broker_position in broker_positions.items():
            if symbol in existing_symbols:
                continue
            recovered_position = Position(
                symbol=broker_position.symbol,
                qty=broker_position.qty,
                name=broker_position.name,
                avg_entry_price=broker_position.avg_price,
                current_price=broker_position.current_price,
                status="OPEN",
                opened_at=utc_now().isoformat(),
            )
            self.positions_repository.upsert(recovered_position)
            self._log_sync_event(
                event_type="position_recovered",
                severity="INFO",
                message="Recovered broker position not present in local storage.",
                payload={"symbol": recovered_position.symbol, "position_id": recovered_position.id},
            )

        open_orders = {item.order_no: item for item in self.kis_client.get_open_orders()}
        for position in self.positions_repository.find_active():
            latest_order = self.orders_repository.find_latest_for_position(position.id)
            if latest_order is None or not latest_order.broker_order_id:
                continue
            open_order = open_orders.get(latest_order.broker_order_id)
            if open_order is None:
                continue
            next_status = "PARTIALLY_FILLED" if open_order.filled_qty > 0 else "ACKNOWLEDGED"
            self.orders_repository.update_status(
                latest_order.id,
                next_status,
                filled_qty=open_order.filled_qty,
                remaining_qty=open_order.remaining_qty,
                last_broker_update_at=utc_now().isoformat(),
            )

        for fill in self.kis_client.get_daily_fills():
            order = self.orders_repository.find_by_broker_order_id(fill.order_no)
            if order is None:
                continue
            if fill.symbol in broker_positions:
                self.fills_repository.create(order.id, fill)
                continue
            self.apply_fill(fill)

    def get_open_positions(self) -> list[Position]:
        return self.positions_repository.find_active()

    def get_position(self, symbol: str) -> Position | None:
        return self.positions_repository.find_by_symbol(symbol)

    def get_position_by_id(self, position_id: int | None) -> Position | None:
        if position_id is None:
            return None
        return self.positions_repository.find_by_id(position_id)

    def apply_fill(self, fill: BrokerFillSnapshot) -> None:
        order = self.orders_repository.find_by_broker_order_id(fill.order_no)
        if order is None:
            return None
        self.fills_repository.create(order.id, fill)

        position = self.get_position_by_id(order.position_id)
        now = fill.filled_at or utc_now().isoformat()
        was_closed = False
        if position is None:
            position = Position(
                symbol=fill.symbol,
                qty=0,
                status="READY",
            )

        if fill.side == "BUY":
            total_cost = (position.avg_entry_price * position.qty) + (fill.fill_price * fill.fill_qty)
            position.qty += fill.fill_qty
            position.avg_entry_price = total_cost / max(position.qty, 1)
            position.current_price = fill.fill_price
            position.status = "OPEN"
            position.opened_at = position.opened_at or now
        else:
            position.qty = max(position.qty - fill.fill_qty, 0)
            position.current_price = fill.fill_price
            if position.qty == 0:
                position.status = "CLOSED"
                position.closed_at = now
                position.exit_reason = order.intent
                was_closed = True
            else:
                position.status = "OPEN"
        self.positions_repository.upsert(position)
        if fill.side == "BUY" and position.opened_at == now:
            self.trade_logs_repository.create_entry(position, order)
        if fill.side == "SELL" and was_closed:
            self.trade_logs_repository.close_trade(position, order, fill.fill_price)

    def snapshot(self) -> PortfolioSnapshot:
        balance = self.kis_client.get_balance()
        return PortfolioSnapshot(
            cash=balance.cash,
            total_asset=balance.total_asset,
            open_positions=self.get_open_positions(),
        )

    def _merge_broker_position(self, local_position: Position, broker_position: BrokerPositionSnapshot) -> None:
        local_position.name = broker_position.name or local_position.name
        local_position.qty = broker_position.qty
        local_position.avg_entry_price = broker_position.avg_price
        local_position.current_price = broker_position.current_price
        if broker_position.qty > 0:
            local_position.status = "OPEN"
        self.positions_repository.upsert(local_position)

    def _log_sync_event(
        self,
        *,
        event_type: str,
        severity: str,
        message: str,
        payload: dict[str, object],
    ) -> None:
        if self.system_events_repository is None:
            return
        self.system_events_repository.create(
            event_type=event_type,
            severity=severity,
            component="portfolio.sync",
            message=message,
            payload=payload,
        )
