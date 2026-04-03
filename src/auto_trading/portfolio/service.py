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
    notifier: object | None = None
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
            self._apply_reconciled_fill(order, fill)

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

    def backfill_missing_trade_log_exits(
        self,
        *,
        use_fill_data: bool = False,
        candidate_order_ids: set[int] | None = None,
    ) -> dict[str, object]:
        filled_exits = self.orders_repository.find_filled_exits_missing_trade_logs()
        backfilled: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for order in filled_exits:
            if candidate_order_ids is not None and order.id not in candidate_order_ids:
                continue
            position = self.get_position_by_id(getattr(order, 'position_id', None))
            if position is None:
                skipped.append({'order_id': order.id, 'symbol': order.symbol, 'reason': 'position_missing'})
                continue
            fill_row = self.fills_repository.find_latest_for_order(order.id) if use_fill_data else None
            self._ensure_trade_entry_for_exit(position, order)
            has_open_trade = getattr(self.trade_logs_repository, 'has_open_trade', None)
            if callable(has_open_trade) and not has_open_trade(position.id):
                skipped.append({'order_id': order.id, 'symbol': order.symbol, 'reason': 'open_trade_missing'})
                continue
            exit_price = self._prepare_position_for_trade_log_exit(position, order, fill_row=fill_row)
            self.trade_logs_repository.close_trade(position, order, exit_price)
            backfilled.append(
                {
                    'order_id': order.id,
                    'symbol': order.symbol,
                    'exit_price': exit_price,
                    'source': 'fill' if fill_row is not None else 'order',
                }
            )
        return {'backfilled': backfilled, 'skipped': skipped}

    def reconcile_eod_daily_fills(self) -> dict[str, object]:
        daily_fills = sorted(
            list(self.kis_client.get_daily_fills()),
            key=lambda item: (self._normalize_fill_timestamp(getattr(item, 'filled_at', '')), getattr(item, 'order_no', ''), getattr(item, 'symbol', '')),
        )
        matched_order_ids: set[int] = set()
        reconciled_order_ids: set[int] = set()
        reconciled_position_ids: set[int] = set()
        unmatched_fills: list[dict[str, object]] = []
        fills_backfilled = 0

        for fill in daily_fills:
            order = self.orders_repository.find_by_broker_order_id(fill.order_no)
            if order is None:
                unmatched_fills.append(
                    {
                        'broker_order_id': fill.order_no,
                        'symbol': fill.symbol,
                        'side': fill.side,
                        'fill_qty': fill.fill_qty,
                        'fill_price': fill.fill_price,
                        'filled_at': fill.filled_at,
                    }
                )
                continue
            matched_order_ids.add(order.id)
            before_status = str(getattr(order, 'status', ''))
            if fill.side == 'SELL':
                position = self.get_position_by_id(getattr(order, 'position_id', None))
                if position is not None:
                    self._ensure_trade_entry_for_exit(position, order)
            if self._apply_reconciled_fill(order, fill):
                fills_backfilled += 1
            refreshed_order = self.orders_repository.find_by_id(order.id)
            if refreshed_order is not None and refreshed_order.status != before_status:
                reconciled_order_ids.add(order.id)
            position_id = getattr(order, 'position_id', None)
            if position_id is not None:
                reconciled_position_ids.add(int(position_id))

        trade_log_result = self.backfill_missing_trade_log_exits(
            use_fill_data=True,
            candidate_order_ids=matched_order_ids if matched_order_ids else set(),
        )
        reconciled_position_ids.update(
            int(getattr(self.orders_repository.find_by_id(item['order_id']), 'position_id', 0) or 0)
            for item in trade_log_result.get('backfilled', [])
            if item.get('order_id') is not None
        )
        reconciled_position_ids.discard(0)

        result = {
            'report_date': datetime.now(timezone(timedelta(hours=9))).date().isoformat(),
            'daily_fill_count': len(daily_fills),
            'fills_backfilled_count': fills_backfilled,
            'matched_order_count': len(matched_order_ids),
            'reconciled_order_count': len(reconciled_order_ids),
            'reconciled_position_count': len(reconciled_position_ids),
            'trade_logs_backfilled_count': len(trade_log_result.get('backfilled', [])),
            'unmatched_fill_count': len(unmatched_fills),
            'unmatched_fills': unmatched_fills,
            'trade_logs_backfilled': trade_log_result.get('backfilled', []),
            'trade_logs_skipped': trade_log_result.get('skipped', []),
        }
        self._log_sync_event(
            event_type='eod_reconcile_completed',
            severity='INFO',
            message='Completed end-of-day reconciliation from broker daily fills.',
            payload=result,
        )
        return result

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
        unresolved_exit_order = self.orders_repository.find_latest_unresolved_exit_for_position(local_position.id)
        if unresolved_exit_order is None:
            return False
        if latest_order is not None and latest_order.side == "SELL" and latest_order.status == "UNKNOWN" and latest_order.broker_order_id:
            unresolved_exit_order = latest_order
        latest_order = unresolved_exit_order
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
                    "broker_order_id": latest_order.broker_order_id,
                    "order_status": latest_order.status,
                    "order_side": latest_order.side,
                    "absence_count": absence_count,
                    "absence_threshold": self.unresolved_sell_absence_threshold,
                    "open_order_found": False,
                    "daily_fill_match_count": 0,
                    "used_exit_price": latest_order.price or local_position.current_price or local_position.avg_entry_price,
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
            payload={
                "symbol": local_position.symbol,
                "position_id": local_position.id,
                "order_id": latest_order.id,
                "broker_order_id": latest_order.broker_order_id,
                "order_status": latest_order.status,
                "order_side": latest_order.side,
                "absence_count": absence_count,
                "absence_threshold": self.unresolved_sell_absence_threshold,
                "open_order_found": False,
                "daily_fill_match_count": 0,
                "used_exit_price": latest_order.price or local_position.current_price or local_position.avg_entry_price,
            },
        )
        self._ensure_trade_entry_for_exit(local_position, latest_order)
        self.trade_logs_repository.close_trade(local_position, latest_order, latest_order.price or local_position.current_price or local_position.avg_entry_price)
        self._notify_order_reconciled_without_fill(
            symbol=local_position.symbol,
            side='SELL',
            qty=latest_order.qty,
            source='브로커 미보유 연속 확인',
            order=latest_order,
            price=latest_order.price or local_position.current_price or local_position.avg_entry_price,
            filled_at=now,
            symbol_name=local_position.name,
            estimated=True,
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
        self._record_estimated_entry_from_broker_state(position, latest_order, broker_position, source='강제 계좌 동기화')

    def record_estimated_entry_recovery(self, order: object, broker_position: BrokerPositionSnapshot, *, source: str) -> None:
        position = self.get_position_by_id(getattr(order, 'position_id', None))
        if position is None:
            return
        position.symbol = broker_position.symbol or position.symbol
        position.name = broker_position.name or position.name
        position.avg_entry_price = broker_position.avg_price or position.avg_entry_price
        position.current_price = broker_position.current_price or position.current_price
        position.qty = max(int(getattr(position, 'qty', 0) or 0), int(getattr(broker_position, 'qty', 0) or 0))
        position.status = 'OPEN'
        self._record_estimated_entry_from_broker_state(position, order, broker_position, source=source)

    def _record_estimated_entry_from_broker_state(self, position: Position, order: object, broker_position: BrokerPositionSnapshot, *, source: str) -> None:
        estimated_at = position.opened_at or utc_now().isoformat()
        position.opened_at = estimated_at
        position.closed_at = None
        position.exit_reason = None
        if broker_position.avg_price:
            position.avg_entry_price = broker_position.avg_price
        if broker_position.current_price:
            position.current_price = broker_position.current_price
        if broker_position.qty:
            position.qty = broker_position.qty
        if broker_position.name:
            position.name = broker_position.name
        position.status = 'OPEN'
        self.positions_repository.upsert(position)
        has_open_trade = getattr(self.trade_logs_repository, 'has_open_trade', None)
        if not callable(has_open_trade) or not has_open_trade(position.id):
            self.trade_logs_repository.create_entry(position, order)
        self._notify_order_reconciled_without_fill(
            symbol=position.symbol,
            side='BUY',
            qty=position.qty,
            source=source,
            order=order,
            price=broker_position.avg_price or getattr(order, 'price', None),
            filled_at=estimated_at,
            symbol_name=position.name,
            estimated=True,
        )

    def _prepare_position_for_trade_log_exit(
        self,
        position: Position,
        order: object,
        *,
        fill_row: dict[str, object] | None = None,
    ) -> float:
        exit_at = ''
        exit_price = 0.0
        if fill_row is not None:
            exit_at = self._normalize_fill_timestamp(str(fill_row.get('filled_at') or ''))
            try:
                exit_price = float(fill_row.get('fill_price') or 0.0)
            except (TypeError, ValueError):
                exit_price = 0.0
        if not exit_at:
            exit_at = getattr(order, 'updated_at', None) or utc_now().isoformat()
        if exit_price <= 0.0:
            exit_price = float(getattr(order, 'price', None) or position.current_price or position.avg_entry_price or 0.0)
        position.qty = 0
        position.status = 'CLOSED'
        position.closed_at = exit_at
        position.current_price = exit_price
        position.exit_reason = getattr(order, 'intent', None) or position.exit_reason or 'EXIT'
        self.positions_repository.upsert(position)
        return exit_price

    def _ensure_trade_entry_for_exit(self, position: Position, order: object) -> None:
        has_open_trade = getattr(self.trade_logs_repository, 'has_open_trade', None)
        if callable(has_open_trade) and has_open_trade(position.id):
            return

        entry_order = self.orders_repository.find_latest_entry_for_position(position.id)
        entry_price = position.avg_entry_price or getattr(entry_order, 'price', None) or position.current_price or 0.0
        entry_qty = int(getattr(order, 'qty', 0) or 0) or int(getattr(position, 'qty', 0) or 0)
        entry_at = position.opened_at
        if not entry_at and entry_order is not None:
            entry_at = getattr(entry_order, 'updated_at', None) or getattr(entry_order, 'created_at', None)

        if entry_order is None:
            return
        if entry_qty <= 0 or float(entry_price) <= 0:
            return

        self.trade_logs_repository.create_entry_snapshot(
            position=position,
            order=entry_order,
            qty=entry_qty,
            entry_price=float(entry_price),
            entry_at=entry_at,
        )
        self._log_sync_event(
            event_type='trade_entry_backfilled',
            severity='INFO',
            message='Backfilled missing trade entry before closing recovered sell order.',
            payload={
                'symbol': position.symbol,
                'position_id': position.id,
                'entry_order_id': entry_order.id,
                'exit_order_id': getattr(order, 'id', None),
            },
        )

    def _notify_order_reconciled_without_fill(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        source: str,
        order: object | None = None,
        price: object | None = None,
        filled_at: str = '',
        symbol_name: str = '',
        estimated: bool = True,
    ) -> None:
        send_trade_recovery = getattr(self.notifier, 'send_trade_recovery', None)
        if not callable(send_trade_recovery):
            return
        payload = {
            'symbol': symbol,
            'symbol_name': symbol_name,
            'side': side,
            'qty': qty,
            'source': source,
            'estimated': estimated,
            'filled_at': filled_at,
        }
        if order is not None:
            payload.update({
                'reason': getattr(order, 'intent', ''),
                'price': price if price is not None else getattr(order, 'price', None),
                'broker_order_id': getattr(order, 'broker_order_id', ''),
            })
        elif price is not None:
            payload['price'] = price
        send_trade_recovery(payload)

    def _notify_trade_fill_from_sync(self, order: object, fill: BrokerFillSnapshot, filled_qty: int, remaining_qty: int) -> None:
        send_trade_fill = getattr(self.notifier, 'send_trade_fill', None)
        if not callable(send_trade_fill):
            return
        position = self.get_position_by_id(getattr(order, 'position_id', None))
        position_qty = position.qty if position is not None else None
        symbol_name = position.name if position is not None else ''
        send_trade_fill(
            {
                'symbol': fill.symbol,
                'symbol_name': symbol_name,
                'side': fill.side,
                'reason': getattr(order, 'intent', ''),
                'fill_qty': fill.fill_qty,
                'fill_price': fill.fill_price,
                'filled_at': fill.filled_at,
                'filled_qty': filled_qty,
                'total_qty': getattr(order, 'qty', 0),
                'remaining_qty': remaining_qty,
                'position_qty': position_qty,
            }
        )

    def _apply_reconciled_fill(self, order: object, fill: BrokerFillSnapshot) -> bool:
        existing_fill_id = self.fills_repository.find_existing_id(order.id, fill)
        if existing_fill_id is not None:
            return False
        next_filled_qty = int(getattr(order, 'filled_qty', 0)) + int(fill.fill_qty)
        remaining_qty = max(int(getattr(order, 'qty', 0)) - next_filled_qty, 0)
        next_status = 'FILLED' if remaining_qty == 0 else 'PARTIALLY_FILLED'
        self.orders_repository.update_status(
            order.id,
            next_status,
            filled_qty=next_filled_qty,
            remaining_qty=remaining_qty,
            last_broker_update_at=fill.filled_at,
        )
        self.apply_fill(fill)
        self._notify_trade_fill_from_sync(order, fill, next_filled_qty, remaining_qty)
        return True

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
            self._ensure_trade_entry_for_exit(local_position, latest_order)
            self.trade_logs_repository.close_trade(local_position, latest_order, latest_order.price or local_position.current_price or local_position.avg_entry_price)
            self._notify_order_reconciled_without_fill(
                symbol=local_position.symbol,
                side='SELL',
                qty=latest_order.qty,
                source='강제 계좌 동기화',
                order=latest_order,
                price=latest_order.price or local_position.current_price or local_position.avg_entry_price,
                filled_at=now,
                symbol_name=local_position.name,
                estimated=True,
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
