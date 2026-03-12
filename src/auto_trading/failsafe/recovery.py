from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RecoveryService:
    portfolio_service: object
    orders_repository: object
    positions_repository: object
    system_events_repository: object | None
    order_engine: object | None
    fail_safe_monitor: object

    def recover(self) -> None:
        self.portfolio_service.sync_from_broker()
        unresolved_orders = self.orders_repository.find_by_statuses(["UNKNOWN"])
        if self.order_engine is not None and unresolved_orders:
            self.order_engine.reconcile_unknown_orders()
        error_positions = self.positions_repository.find_by_statuses(["ERROR"])
        self._reconcile_error_positions(error_positions)
        remaining_unknown_orders = self.orders_repository.find_by_statuses(["UNKNOWN"])
        remaining_error_positions = self.positions_repository.find_by_statuses(["ERROR"])
        if remaining_unknown_orders or remaining_error_positions:
            self.fail_safe_monitor.blocked = True
            self.fail_safe_monitor.fallback_active = True
            self._log_event(
                event_type="recovery_incomplete",
                severity="WARN",
                message="Recovery completed with unresolved orders or positions.",
                payload={
                    "unknown_orders": len(remaining_unknown_orders),
                    "error_positions": len(remaining_error_positions),
                },
            )
            return
        self.fail_safe_monitor.blocked = False
        self.fail_safe_monitor.fallback_active = False
        self._log_event(
            event_type="recovery_completed",
            severity="INFO",
            message="Recovery completed successfully.",
            payload={},
        )

    def _reconcile_error_positions(self, positions: list[object]) -> None:
        broker_positions = {item.symbol: item for item in self.portfolio_service.kis_client.get_positions()}
        for position in positions:
            broker_position = broker_positions.get(position.symbol)
            if broker_position is None:
                if position.qty <= 0:
                    position.status = "READY"
                else:
                    position.status = "CLOSED"
                    position.closed_at = position.closed_at or position.updated_at
                self.positions_repository.upsert(position)
                continue
            position.qty = broker_position.qty
            position.avg_entry_price = broker_position.avg_price
            position.current_price = broker_position.current_price
            position.status = "OPEN"
            self.positions_repository.upsert(position)

    def _log_event(
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
            component="failsafe.recovery",
            message=message,
            payload=payload,
        )
