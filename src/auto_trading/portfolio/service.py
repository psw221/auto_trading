from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    unresolved_sell_absence_threshold: int = 2

    def sync_from_broker(self) -> None:
        broker_positions = {item.symbol: item for item in self.kis_client.get_positions()}
        open_orders = {item.order_no: item for item in self.kis_client.get_open_orders()}
        daily_fills = list(self.kis_client.get_daily_fills())
        local_positions = self.positions_repository.find_all()
        local_positions = self._compact_duplicate_local_positions(local_positions)

        merged_symbols: set[str] = set()
        positions_by_symbol: dict[str, list[Position]] = {}
        for local_position in local_positions:
            positions_by_symbol.setdefault(local_position.symbol, []).append(local_position)
            broker_position = broker_positions.get(local_position.symbol)
            if broker_position is None:
                if self._force_close_missing_position_after_unresolved_exit(
                    local_position,
                    open_orders=open_orders,
                    daily_fills=daily_fills,
                ):
                    merged_symbols.add(local_position.symbol)
                    continue
                if local_position.status in {"OPEN", "OPENING", "CLOSING"} and local_position.qty > 0:
                    self._log_sync_event(
                        event_type="position_mismatch",
                        severity="WARN",
                        message="Broker holdings did not include a locally tracked active position during sync. Keeping local position for retry.",
                        payload={"symbol": local_position.symbol, "position_id": local_position.id},
                    )
                continue
            if local_position.symbol in merged_symbols:
                continue
            was_active = local_position.status in {"OPEN", "OPENING", "CLOSING"} and local_position.qty > 0
            self._merge_broker_position(local_position, broker_position)
            if not was_active:
                self._log_sync_event(
                    event_type="position_recovered",
                    severity="INFO",
                    message="Recovered broker position into existing local row.",
                    payload={"symbol": local_position.symbol, "position_id": local_position.id},
                )
            merged_symbols.add(local_position.symbol)

        for symbol, broker_position in broker_positions.items():
            if symbol in merged_symbols:
                continue
            restored_positions = positions_by_symbol.get(symbol, [])
            if restored_positions:
                restored_positions.sort(key=lambda item: (item.updated_at or "", item.id or 0), reverse=True)
                restored_position = restored_positions[0]
                self._merge_broker_position(restored_position, broker_position)
                self._log_sync_event(
                    event_type="position_recovered",
                    severity="INFO",
                    message="Recovered broker position into existing local row.",
                    payload={"symbol": restored_position.symbol, "position_id": restored_position.id},
                )
                merged_symbols.add(symbol)
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

        for fill in daily_fills:
            order = self.orders_repository.find_by_broker_order_id(fill.order_no)
            if order is None:
                continue
            if fill.symbol in broker_positions:
                self.fills_repository.create(order.id, fill)
                continue
            self.apply_fill(fill)

    def force_sync_from_broker(
        self,
        *,
        dry_run: bool = False,
        allow_empty: bool = False,
        confirm_rounds: int = 2,
    ) -> dict[str, object]:
        confirm_rounds = max(int(confirm_rounds or 1), 1)
        broker_snapshots: list[dict[str, BrokerPositionSnapshot]] = []
        for _ in range(confirm_rounds):
            broker_snapshots.append({item.symbol: item for item in self.kis_client.get_positions()})
        broker_positions = broker_snapshots[-1]
        stable = all(self._broker_positions_signature(snapshot) == self._broker_positions_signature(broker_positions) for snapshot in broker_snapshots)
        if not stable:
            return {
                "applied": False,
                "aborted_reason": "unstable_broker_positions",
                "broker_symbols": sorted(broker_positions.keys()),
                "closed_symbols": [],
                "recovered_symbols": [],
                "created_symbols": [],
            }
        if not broker_positions and not allow_empty:
            return {
                "applied": False,
                "aborted_reason": "empty_broker_positions",
                "broker_symbols": [],
                "closed_symbols": [],
                "recovered_symbols": [],
                "created_symbols": [],
            }

        local_positions = self.positions_repository.find_all()
        local_positions = self._compact_duplicate_local_positions(local_positions)
        positions_by_symbol: dict[str, list[Position]] = {}
        for local_position in local_positions:
            positions_by_symbol.setdefault(local_position.symbol, []).append(local_position)

        closed_symbols: list[str] = []
        recovered_symbols: list[str] = []
        created_symbols: list[str] = []

        for symbol, broker_position in broker_positions.items():
            existing_positions = positions_by_symbol.get(symbol, [])
            if existing_positions:
                existing_positions.sort(key=lambda item: (item.updated_at or '', item.id or 0), reverse=True)
                restored = existing_positions[0]
                was_active = restored.status in {"OPEN", "OPENING", "CLOSING"} and restored.qty > 0
                if not dry_run:
                    self._merge_broker_position(restored, broker_position)
                if not was_active:
                    recovered_symbols.append(symbol)
                    if not dry_run:
                        self._log_sync_event(
                            event_type="position_force_recovered",
                            severity="INFO",
                            message="Recovered local position from authoritative broker sync.",
                            payload={"symbol": symbol, "position_id": restored.id},
                        )
                if not dry_run:
                    self._reconcile_latest_order_from_authoritative_position(restored, broker_position)
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
            created_symbols.append(symbol)
            if not dry_run:
                self.positions_repository.upsert(recovered_position)
                self._log_sync_event(
                    event_type="position_force_created",
                    severity="INFO",
                    message="Created local position from authoritative broker sync.",
                    payload={"symbol": symbol, "position_id": recovered_position.id},
                )
                self._reconcile_latest_order_from_authoritative_position(recovered_position, broker_position)

        for symbol, existing_positions in positions_by_symbol.items():
            if symbol in broker_positions:
                continue
            for local_position in existing_positions:
                if local_position.status not in {"OPEN", "OPENING", "CLOSING"} or local_position.qty <= 0:
                    continue
                latest_order = self.orders_repository.find_latest_for_position(local_position.id)
                if not dry_run:
                    self._force_close_position_from_authoritative_sync(local_position, latest_order)
                if symbol not in closed_symbols:
                    closed_symbols.append(symbol)

        return {
            "applied": not dry_run,
            "aborted_reason": "",
            "broker_symbols": sorted(broker_positions.keys()),
            "closed_symbols": closed_symbols,
            "recovered_symbols": recovered_symbols,
            "created_symbols": created_symbols,
            "dry_run": dry_run,
            "confirm_rounds": confirm_rounds,
        }

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
        now = self._normalize_fill_timestamp(fill.filled_at)
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

    @staticmethod
    def _normalize_fill_timestamp(value: str) -> str:
        raw = str(value or '').strip()
        if not raw:
            return utc_now().isoformat()
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            if len(raw) == 6 and raw.isdigit():
                seoul = timezone(timedelta(hours=9))
                now_local = utc_now().astimezone(seoul)
                try:
                    parsed = datetime(
                        now_local.year,
                        now_local.month,
                        now_local.day,
                        int(raw[0:2]),
                        int(raw[2:4]),
                        int(raw[4:6]),
                        tzinfo=seoul,
                    )
                except ValueError:
                    return utc_now().isoformat()
            else:
                return utc_now().isoformat()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()

    def _merge_broker_position(self, local_position: Position, broker_position: BrokerPositionSnapshot) -> None:
        local_position.name = broker_position.name or local_position.name
        local_position.qty = broker_position.qty
        local_position.avg_entry_price = broker_position.avg_price
        local_position.current_price = broker_position.current_price
        if broker_position.qty > 0:
            local_position.status = "OPEN"
            local_position.closed_at = None
            local_position.exit_reason = None
        self.positions_repository.upsert(local_position)

    def _force_close_missing_position_after_unresolved_exit(
        self,
        local_position: Position,
        *,
        open_orders: dict[str, object],
        daily_fills: list[BrokerFillSnapshot],
    ) -> bool:
        if local_position.status not in {"OPEN", "OPENING", "CLOSING"} or local_position.qty <= 0:
            return False
        latest_order = self.orders_repository.find_latest_for_position(local_position.id)
        if latest_order is None:
            return False
        if latest_order.side != "SELL" or latest_order.status != "UNKNOWN" or not latest_order.broker_order_id:
            return False
        if latest_order.broker_order_id in open_orders:
            return False
        if any(fill.order_no == latest_order.broker_order_id for fill in daily_fills):
            return False

        now = utc_now().isoformat()
        absence_count = self._next_unresolved_sell_absence_count(latest_order.failure_reason)
        if absence_count < self.unresolved_sell_absence_threshold:
            self.orders_repository.update_status(
                latest_order.id,
                "UNKNOWN",
                last_broker_update_at=now,
                failure_reason=self._encode_unresolved_sell_absence_count(absence_count),
            )
            self._log_sync_event(
                event_type="broker_position_absent_after_unresolved_sell_check",
                severity="INFO",
                message="Broker still does not report holdings for unresolved sell order. Waiting for repeated confirmation.",
                payload={
                    "symbol": local_position.symbol,
                    "position_id": local_position.id,
                    "order_id": latest_order.id,
                    "absence_count": absence_count,
                },
            )
            return False

        local_position.qty = 0
        local_position.status = "CLOSED"
        local_position.closed_at = now
        local_position.exit_reason = "broker_position_absent_after_sell"
        if latest_order.price is not None:
            local_position.current_price = latest_order.price
        self.positions_repository.upsert(local_position)
        self.orders_repository.update_status(
            latest_order.id,
            "FILLED",
            filled_qty=latest_order.qty,
            remaining_qty=0,
            last_broker_update_at=now,
            failure_reason=f"Inferred after {absence_count} consecutive missing broker position checks.",
        )
        self._log_sync_event(
            event_type="position_closed_from_broker_absence",
            severity="WARN",
            message="Closed local position because broker no longer reports holdings after repeated unresolved sell checks.",
            payload={"symbol": local_position.symbol, "position_id": local_position.id, "order_id": latest_order.id, "absence_count": absence_count},
        )
        return True

    @staticmethod
    def _broker_positions_signature(snapshot: dict[str, BrokerPositionSnapshot]) -> tuple[tuple[str, int, float], ...]:
        return tuple(sorted((symbol, int(item.qty), float(item.avg_price)) for symbol, item in snapshot.items()))

    @staticmethod
    def _next_unresolved_sell_absence_count(failure_reason: str | None) -> int:
        raw = str(failure_reason or '').strip()
        prefix = 'absence_check:'
        if raw.startswith(prefix):
            remainder = raw[len(prefix):].split('|', 1)[0].strip()
            if remainder.isdigit():
                return int(remainder) + 1
        return 1

    @staticmethod
    def _encode_unresolved_sell_absence_count(count: int) -> str:
        return f'absence_check:{max(count, 1)}|Broker position absent for unresolved sell order.'

    def _reconcile_latest_order_from_authoritative_position(
        self,
        position: Position,
        broker_position: BrokerPositionSnapshot,
    ) -> None:
        latest_order = self.orders_repository.find_latest_for_position(position.id)
        if latest_order is None or latest_order.side != "BUY":
            return
        if latest_order.status not in {"UNKNOWN", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"}:
            return
        self.orders_repository.update_status(
            latest_order.id,
            "FILLED",
            filled_qty=max(latest_order.qty, broker_position.qty),
            remaining_qty=0,
            last_broker_update_at=utc_now().isoformat(),
            failure_reason="Reconciled from authoritative broker sync.",
        )
        self._log_sync_event(
            event_type="order_force_reconciled",
            severity="INFO",
            message="Marked buy order as filled from authoritative broker sync.",
            payload={"symbol": position.symbol, "position_id": position.id, "order_id": latest_order.id},
        )

    def _force_close_position_from_authoritative_sync(self, local_position: Position, latest_order: object | None) -> None:
        now = utc_now().isoformat()
        closed_qty = local_position.qty
        local_position.qty = 0
        local_position.status = "CLOSED"
        local_position.closed_at = now
        local_position.exit_reason = "force_broker_sync_absent"
        self.positions_repository.upsert(local_position)
        if latest_order is not None and latest_order.side == "SELL" and latest_order.status in {"UNKNOWN", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"}:
            self.orders_repository.update_status(
                latest_order.id,
                "FILLED",
                filled_qty=latest_order.qty,
                remaining_qty=0,
                last_broker_update_at=now,
                failure_reason="Reconciled from authoritative broker sync.",
            )
        self._log_sync_event(
            event_type="position_force_closed",
            severity="WARN",
            message="Closed local position because authoritative broker sync did not report holdings.",
            payload={"symbol": local_position.symbol, "position_id": local_position.id, "closed_qty": closed_qty},
        )

    def _compact_duplicate_local_positions(self, local_positions: list[Position]) -> list[Position]:
        grouped: dict[str, list[Position]] = {}
        for position in local_positions:
            grouped.setdefault(position.symbol, []).append(position)

        compacted: list[Position] = []
        for symbol, positions in grouped.items():
            active_positions = [
                position for position in positions
                if position.status in {"OPENING", "OPEN", "CLOSING", "ERROR"} or position.qty > 0
            ]
            if len(active_positions) <= 1:
                compacted.extend(positions)
                continue

            active_positions.sort(key=lambda item: (item.updated_at or "", item.id or 0), reverse=True)
            keeper = active_positions[0]
            compacted.append(keeper)
            active_ids = {item.id for item in active_positions}
            for duplicate in active_positions[1:]:
                duplicate.qty = 0
                duplicate.status = "CLOSED"
                duplicate.closed_at = utc_now().isoformat()
                duplicate.exit_reason = "duplicate_local_position"
                self.positions_repository.upsert(duplicate)
                self._log_sync_event(
                    event_type="duplicate_local_position",
                    severity="WARN",
                    message="Compacted duplicate local positions for symbol during sync.",
                    payload={
                        "symbol": symbol,
                        "position_id": duplicate.id,
                        "kept_position_id": keeper.id,
                    },
                )
            compacted.extend([position for position in positions if position.id not in active_ids])

        compacted.sort(key=lambda item: (item.updated_at or "", item.id or 0), reverse=True)
        return compacted

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
