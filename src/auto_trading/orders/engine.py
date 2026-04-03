from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from auto_trading.broker.dto import (
    BrokerFillSnapshot,
    BrokerOrderRequest,
    BrokerRealtimeEvent,
    BrokerReviseCancelRequest,
)
from auto_trading.common.exceptions import BrokerApiError, BrokerResponseError
from auto_trading.common.time import utc_now
from auto_trading.orders.models import Order
from auto_trading.portfolio.models import Position


@dataclass(slots=True)
class OrderEngine:
    kis_client: object
    orders_repository: object
    positions_repository: object
    portfolio_service: object
    system_events_repository: object
    notifier: object
    fail_safe_monitor: object
    stale_unknown_order_seconds: int = 3600

    def submit_entry(self, signal: object, sizing: object) -> Order:
        if self.fail_safe_monitor.should_block_new_orders():
            self.system_events_repository.create(
                event_type="order_blocked",
                severity="ERROR",
                component="orders.engine",
                message="Fail-safe is blocking new entry orders.",
                payload={"symbol": signal.symbol},
            )
            raise RuntimeError("Fail-safe is blocking new orders.")

        existing_position = self.positions_repository.find_active_by_symbol(signal.symbol)
        if existing_position and existing_position.status in {"OPENING", "OPEN", "CLOSING"}:
            self.system_events_repository.create(
                event_type="duplicate_position",
                severity="WARN",
                component="orders.engine",
                message="Entry skipped because an active position already exists.",
                payload={"symbol": signal.symbol, "status": existing_position.status},
            )
            raise RuntimeError(f"Active position already exists for {signal.symbol}.")

        position = Position(
            symbol=signal.symbol,
            qty=0,
            score_at_entry=signal.score_total,
            target_weight=None,
            status="OPENING",
        )
        self.positions_repository.upsert(position)
        order = Order(
            symbol=signal.symbol,
            side="BUY",
            qty=sizing.qty,
            order_type=sizing.order_type,
            price=sizing.price,
            intent="ENTRY",
            position_id=position.id,
        )
        self.orders_repository.create(order)
        try:
            response = self.kis_client.place_cash_order(
                BrokerOrderRequest(
                    symbol=signal.symbol,
                    side="BUY",
                    qty=sizing.qty,
                    order_type=sizing.order_type,
                    price=sizing.price,
                )
            )
        except (BrokerApiError, BrokerResponseError) as exc:
            self._handle_broker_exception(order, position, exc, restore_status="ERROR")
            return order
        self._apply_submission_result(
            order=order,
            response=response,
            reject_position=position,
            reject_restore_status="ERROR",
            reject_message="Entry order rejected.",
        )
        return order

    def submit_exit(self, signal: object, position: object) -> Order:
        position.status = "CLOSING"
        position.exit_reason = signal.reason
        self.positions_repository.upsert(position)
        order = Order(
            symbol=position.symbol,
            side="SELL",
            qty=position.qty,
            order_type=signal.order_type,
            intent=signal.reason.upper(),
            position_id=position.id,
            price=getattr(signal, "price", None),
        )
        self.orders_repository.create(order)
        try:
            response = self.kis_client.place_cash_order(
                BrokerOrderRequest(
                    symbol=position.symbol,
                    side="SELL",
                    qty=position.qty,
                    order_type=signal.order_type,
                    price=getattr(signal, "price", None),
                )
            )
        except (BrokerApiError, BrokerResponseError) as exc:
            self._handle_broker_exception(order, position, exc, restore_status="OPEN")
            return order
        self._apply_submission_result(
            order=order,
            response=response,
            reject_position=position,
            reject_restore_status="OPEN",
            reject_message="Exit order rejected.",
        )
        return order

    def revise_entry_order(self, order: Order, new_price: float) -> None:
        if not order.broker_order_id:
            self._mark_unknown(order, "Order cannot be revised without broker order number.")
            return
        self.orders_repository.update_status(
            order.id,
            "PENDING_REPLACE",
            last_broker_update_at=utc_now().isoformat(),
        )
        try:
            response = self.kis_client.revise_or_cancel_order(
                BrokerReviseCancelRequest(
                    orig_odno=order.broker_order_id,
                    symbol=order.symbol,
                    qty=order.remaining_qty,
                    mode="REVISE",
                    price=new_price,
                )
            )
        except (BrokerApiError, BrokerResponseError) as exc:
            self.fail_safe_monitor.on_api_error(exc)
            self._mark_unknown(order, str(exc))
            return
        if response.accepted:
            self.orders_repository.update_status(
                order.id,
                "REPLACED",
                broker_order_id=response.order_no or order.broker_order_id,
                last_broker_update_at=utc_now().isoformat(),
            )
            return
        self._mark_unknown(order, response.msg or "Revise request returned ambiguous result.")

    def cancel_order(self, order: Order) -> None:
        if not order.broker_order_id:
            self._mark_unknown(order, "Order cannot be canceled without broker order number.")
            return
        self.orders_repository.update_status(
            order.id,
            "PENDING_CANCEL",
            last_broker_update_at=utc_now().isoformat(),
        )
        try:
            response = self.kis_client.revise_or_cancel_order(
                BrokerReviseCancelRequest(
                    orig_odno=order.broker_order_id,
                    symbol=order.symbol,
                    qty=order.remaining_qty,
                    mode="CANCEL",
                )
            )
        except (BrokerApiError, BrokerResponseError) as exc:
            self.fail_safe_monitor.on_api_error(exc)
            self._mark_unknown(order, str(exc))
            return
        if response.accepted:
            self.orders_repository.update_status(
                order.id,
                "CANCELED",
                last_broker_update_at=utc_now().isoformat(),
            )
            return
        self._mark_unknown(order, response.msg or "Cancel request returned ambiguous result.")

    def handle_broker_event(self, event: BrokerRealtimeEvent) -> None:
        if event.event_type == "fill":
            fill = BrokerFillSnapshot(
                order_no=event.payload.get("order_no", ""),
                symbol=event.symbol or event.payload.get("symbol", ""),
                side=event.payload.get("side", ""),
                fill_qty=int(event.payload.get("fill_qty", "0")),
                fill_price=float(event.payload.get("fill_price", "0")),
                filled_at=event.payload.get("filled_at", utc_now().isoformat()),
            )
            self.system_events_repository.create(
                event_type="broker_fill_received",
                severity="INFO",
                component="orders.engine",
                message="Received broker fill event from realtime stream.",
                payload={
                    "order_no": fill.order_no,
                    "symbol": fill.symbol,
                    "side": fill.side,
                    "fill_qty": fill.fill_qty,
                    "fill_price": fill.fill_price,
                    "filled_at": fill.filled_at,
                },
            )
            order = self.orders_repository.find_by_broker_order_id(fill.order_no)
            if order is None:
                self.system_events_repository.create(
                    event_type="broker_fill_unmatched_order",
                    severity="WARN",
                    component="orders.engine",
                    message="Received broker fill event but could not match it to a local order.",
                    payload={
                        "order_no": fill.order_no,
                        "symbol": fill.symbol,
                        "side": fill.side,
                        "fill_qty": fill.fill_qty,
                        "fill_price": fill.fill_price,
                        "filled_at": fill.filled_at,
                    },
                )
                return None
            next_filled_qty = order.filled_qty + fill.fill_qty
            remaining_qty = max(order.qty - next_filled_qty, 0)
            next_status = "FILLED" if remaining_qty == 0 else "PARTIALLY_FILLED"
            self.orders_repository.update_status(
                order.id,
                next_status,
                filled_qty=next_filled_qty,
                remaining_qty=remaining_qty,
                last_broker_update_at=fill.filled_at,
            )
            self.portfolio_service.apply_fill(fill)
            self.notifier.send_trade_fill(
                self._build_fill_notification_payload(
                    fill=fill,
                    order=order,
                    filled_qty=next_filled_qty,
                    remaining_qty=remaining_qty,
                )
            )
            return None

        broker_order_id = event.payload.get("order_no")
        if not broker_order_id:
            return None
        order = self.orders_repository.find_by_broker_order_id(broker_order_id)
        if order is None:
            return None
        status = self._normalize_event_status(event.payload.get("status", ""))
        if status in {"ACKNOWLEDGED", "CANCELED", "REJECTED", "UNKNOWN"}:
            self.orders_repository.update_status(
                order.id,
                status,
                last_broker_update_at=utc_now().isoformat(),
                failure_reason=event.payload.get("message"),
            )
            if status == "CANCELED":
                self._restore_position_after_cancel(order)
            if status in {"REJECTED", "UNKNOWN"}:
                self.system_events_repository.create(
                    event_type=f"order_{status.lower()}",
                    severity="ERROR" if status == "REJECTED" else "WARN",
                    component="orders.engine",
                    message=event.payload.get("message", f"Order moved to {status}."),
                    payload={"order_id": order.id, "broker_order_id": broker_order_id},
                )

    def reconcile_unknown_orders(self) -> None:
        try:
            open_orders = self.kis_client.get_open_orders()
            daily_fills = self.kis_client.get_daily_fills()
            broker_positions = {item.symbol: item for item in self.kis_client.get_positions()}
        except (BrokerApiError, BrokerResponseError) as exc:
            self.fail_safe_monitor.on_api_error(exc)
            self.system_events_repository.create(
                event_type="reconcile_failed",
                severity="ERROR",
                component="orders.engine",
                message=str(exc),
                payload={},
            )
            return
        fill_map = {}
        for fill in daily_fills:
            fill_map.setdefault(fill.order_no, []).append(fill)
        retried_daily_fills = False
        for order in self.orders_repository.find_reconcilable_orders():
            matched = next((item for item in open_orders if item.order_no == order.broker_order_id), None)
            fill_matched = list(fill_map.get(order.broker_order_id, []))
            used_daily_fills_retry = False
            if not fill_matched and matched is None and not retried_daily_fills:
                try:
                    daily_fills_retry = self.kis_client.get_daily_fills()
                except (BrokerApiError, BrokerResponseError):
                    daily_fills_retry = []
                else:
                    retried_daily_fills = True
                    used_daily_fills_retry = True
                    fill_map.clear()
                    for fill in daily_fills_retry:
                        fill_map.setdefault(fill.order_no, []).append(fill)
                    fill_matched = list(fill_map.get(order.broker_order_id, []))
            if fill_matched:
                latest_fill = fill_matched[-1]
                total_fill_qty = sum(item.fill_qty for item in fill_matched)
                remaining_qty = max(order.qty - total_fill_qty, 0)
                next_status = "FILLED" if remaining_qty == 0 else "PARTIALLY_FILLED"
                self.orders_repository.update_status(
                    order.id,
                    next_status,
                    filled_qty=total_fill_qty,
                    remaining_qty=remaining_qty,
                    last_broker_update_at=latest_fill.filled_at,
                )
                self._apply_reconciled_fills(order, fill_matched, total_fill_qty, remaining_qty)
                continue
            if matched is None:
                broker_position = broker_positions.get(order.symbol)
                if order.side == "BUY" and broker_position is not None and broker_position.qty > 0:
                    self.orders_repository.update_status(
                        order.id,
                        "FILLED",
                        filled_qty=order.qty,
                        remaining_qty=0,
                        last_broker_update_at=utc_now().isoformat(),
                        failure_reason="Recovered from broker holdings during order reconciliation.",
                    )
                    self.system_events_repository.create(
                        event_type="unknown_buy_order_recovered",
                        severity="INFO",
                        component="orders.engine",
                        message="Recovered buy order from broker holdings during reconciliation.",
                        payload={
                            "order_id": order.id,
                            "broker_order_id": order.broker_order_id,
                            "symbol": order.symbol,
                            "order_side": order.side,
                            "order_status": order.status,
                            "open_order_found": False,
                            "daily_fill_match_count": len(fill_matched),
                            "used_daily_fills_retry": used_daily_fills_retry,
                            "broker_position_found": True,
                            "broker_position_qty": broker_position.qty,
                        },
                    )
                    record_estimated_entry = getattr(self.portfolio_service, 'record_estimated_entry_recovery', None)
                    if callable(record_estimated_entry):
                        record_estimated_entry(order, broker_position, source='브로커 보유 기준 주문 복구')
                    else:
                        self._notify_order_recovered_from_broker_holdings(order, broker_position.qty)
                    continue
                if order.status == "UNKNOWN":
                    if self._should_close_stale_unknown_order(order, broker_positions):
                        self.orders_repository.update_status(
                            order.id,
                            "FAILED",
                            last_broker_update_at=utc_now().isoformat(),
                            failure_reason="Closed stale unknown order after repeated unresolved reconciliation checks.",
                        )
                        self.system_events_repository.create(
                            event_type="stale_unknown_order_closed",
                            severity="WARN",
                            component="orders.engine",
                            message="Closed stale unknown order after it remained unresolved beyond the cleanup threshold.",
                            payload={
                                "order_id": order.id,
                                "broker_order_id": order.broker_order_id,
                                "symbol": order.symbol,
                                "order_side": order.side,
                                "order_status": order.status,
                                "open_order_found": False,
                                "daily_fill_match_count": len(fill_matched),
                                "used_daily_fills_retry": used_daily_fills_retry,
                                "broker_position_found": bool(order.symbol in broker_positions),
                            },
                        )
                    else:
                        self.system_events_repository.create(
                            event_type="unknown_order_unresolved",
                            severity="WARN",
                            component="orders.engine",
                            message="Unknown order could not be resolved from broker open orders.",
                            payload={
                                "order_id": order.id,
                                "broker_order_id": order.broker_order_id,
                                "symbol": order.symbol,
                                "order_side": order.side,
                                "order_status": order.status,
                                "open_order_found": False,
                                "daily_fill_match_count": len(fill_matched),
                                "used_daily_fills_retry": used_daily_fills_retry,
                                "broker_position_found": bool(order.symbol in broker_positions),
                            },
                        )
                else:
                    self.orders_repository.update_status(
                        order.id,
                        "UNKNOWN",
                        last_broker_update_at=utc_now().isoformat(),
                        failure_reason="Submitted order not found in broker open orders or daily fills.",
                    )
                    self.system_events_repository.create(
                        event_type="submitted_order_unresolved",
                        severity="WARN",
                        component="orders.engine",
                        message="Submitted order not found in broker open orders or daily fills.",
                        payload={
                            "order_id": order.id,
                            "broker_order_id": order.broker_order_id,
                            "symbol": order.symbol,
                            "order_side": order.side,
                            "order_status": order.status,
                            "open_order_found": False,
                            "daily_fill_match_count": len(fill_matched),
                            "used_daily_fills_retry": used_daily_fills_retry,
                            "broker_position_found": bool(order.symbol in broker_positions),
                        },
                    )
                continue
            next_status = "PARTIALLY_FILLED" if matched.filled_qty > 0 else "ACKNOWLEDGED"
            self.orders_repository.update_status(
                order.id,
                next_status,
                filled_qty=matched.filled_qty,
                remaining_qty=matched.remaining_qty,
                last_broker_update_at=utc_now().isoformat(),
            )

    def _should_close_stale_unknown_order(self, order: Order, broker_positions: dict[str, object]) -> bool:
        if order.status != 'UNKNOWN':
            return False
        symbol = getattr(order, 'symbol', '')
        if order.side == 'BUY' and symbol and symbol in broker_positions:
            return False
        position = None
        if getattr(order, 'position_id', None) is not None:
            position = self.positions_repository.find_by_id(order.position_id)
        if position is not None and getattr(position, 'status', '') in {'OPEN', 'OPENING', 'CLOSING'} and getattr(position, 'qty', 0) > 0:
            return False
        created_at = str(getattr(order, 'created_at', '') or '')
        if not created_at:
            return False
        try:
            created = __import__('datetime').datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=utc_now().tzinfo)
        return (utc_now() - created) >= timedelta(seconds=self.stale_unknown_order_seconds)

    def _notify_order_recovered_from_broker_holdings(self, order: Order, broker_qty: int) -> None:
        send_system_event = getattr(self.notifier, 'send_system_event', None)
        if not callable(send_system_event):
            return
        send_system_event(
            {
                'severity': 'INFO',
                'component': 'orders.engine',
                'message': f"체결 알림을 받지 못한 주문을 브로커 보유 기준으로 복구했습니다. symbol={order.symbol} qty={broker_qty}",
            }
        )

    def _apply_reconciled_fills(
        self,
        order: Order,
        fills: list[BrokerFillSnapshot],
        total_fill_qty: int,
        remaining_qty: int,
    ) -> None:
        fills_repository = getattr(self.portfolio_service, 'fills_repository', None)
        current_filled_qty = order.filled_qty
        for fill in fills:
            existing_fill_id = None
            if fills_repository is not None:
                existing_fill_id = fills_repository.find_existing_id(order.id, fill)
            if existing_fill_id is not None:
                continue
            current_filled_qty += fill.fill_qty
            self.portfolio_service.apply_fill(fill)
            self.notifier.send_trade_fill(
                self._build_fill_notification_payload(
                    fill=fill,
                    order=order,
                    filled_qty=min(current_filled_qty, total_fill_qty),
                    remaining_qty=max(order.qty - current_filled_qty, 0) if remaining_qty != 0 else 0,
                )
            )

    def _apply_submission_result(
        self,
        *,
        order: Order,
        response: object,
        reject_position: Position,
        reject_restore_status: str,
        reject_message: str,
    ) -> None:
        now = utc_now().isoformat()
        if response.accepted and response.order_no:
            order.broker_order_id = response.order_no
            order.submitted_at = now
            self.orders_repository.update_status(
                order.id,
                "SUBMITTED",
                broker_order_id=response.order_no,
                submitted_at=now,
                last_broker_update_at=now,
            )
            order.status = "SUBMITTED"
            return
        if response.accepted and not response.order_no:
            self._mark_unknown(order, "Broker accepted request but did not return order number.")
            return
        self.orders_repository.update_status(order.id, "REJECTED", failure_reason=response.msg)
        order.status = "REJECTED"
        reject_position.status = reject_restore_status
        reject_position.exit_reason = response.msg
        self.positions_repository.upsert(reject_position)
        self.system_events_repository.create(
            event_type="order_rejected",
            severity="ERROR",
            component="orders.engine",
            message=response.msg or reject_message,
            payload={"symbol": order.symbol, "side": order.side, "order_id": order.id},
        )

    def _mark_unknown(self, order: Order, message: str) -> None:
        self.orders_repository.update_status(
            order.id,
            "UNKNOWN",
            last_broker_update_at=utc_now().isoformat(),
            failure_reason=message,
        )
        self.system_events_repository.create(
            event_type="order_unknown",
            severity="WARN",
            component="orders.engine",
            message=message,
            payload={"order_id": order.id, "broker_order_id": order.broker_order_id},
        )

    def _handle_broker_exception(
        self,
        order: Order,
        position: Position,
        exc: Exception,
        *,
        restore_status: str,
    ) -> None:
        self.fail_safe_monitor.on_api_error(exc)
        self.orders_repository.update_status(
            order.id,
            "UNKNOWN",
            last_broker_update_at=utc_now().isoformat(),
            failure_reason=str(exc),
        )
        order.status = "UNKNOWN"
        position.status = restore_status
        position.exit_reason = str(exc)
        self.positions_repository.upsert(position)
        self.system_events_repository.create(
            event_type="broker_exception",
            severity="ERROR",
            component="orders.engine",
            message=str(exc),
            payload={"order_id": order.id, "symbol": order.symbol},
        )

    def _restore_position_after_cancel(self, order: Order) -> None:
        if order.position_id is None:
            return
        position = self.positions_repository.find_by_id(order.position_id)
        if position is None:
            return
        position.status = "READY" if order.side == "BUY" and position.qty == 0 else "OPEN"
        self.positions_repository.upsert(position)

    def _build_fill_notification_payload(
        self,
        *,
        fill: BrokerFillSnapshot,
        order: Order,
        filled_qty: int,
        remaining_qty: int,
    ) -> dict[str, object]:
        position = self.portfolio_service.get_position_by_id(order.position_id)
        position_qty = position.qty if position is not None else None
        symbol_name = position.name if position is not None else ""
        return {
            "symbol": fill.symbol,
            "symbol_name": symbol_name,
            "side": fill.side,
            "reason": order.intent,
            "fill_qty": fill.fill_qty,
            "fill_price": fill.fill_price,
            "filled_at": fill.filled_at,
            "filled_qty": filled_qty,
            "total_qty": order.qty,
            "remaining_qty": remaining_qty,
            "position_qty": position_qty,
        }

    @staticmethod
    def _normalize_event_status(status: str) -> str:
        mapping = {
            "RECEIVED": "ACKNOWLEDGED",
            "ACK": "ACKNOWLEDGED",
            "ACKNOWLEDGED": "ACKNOWLEDGED",
            "CANCELED": "CANCELED",
            "CANCELLED": "CANCELED",
            "REJECTED": "REJECTED",
            "UNKNOWN": "UNKNOWN",
        }
        return mapping.get(status.upper(), status.upper())
