from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from auto_trading.broker.kis_ws_client import KISWebSocketClient
from auto_trading.failsafe.monitor import FailSafeMonitor
from auto_trading.market_data.collector import MarketDataCollector
from auto_trading.orders.engine import OrderEngine


@dataclass(slots=True)
class RuntimeService:
    kis_ws_client: KISWebSocketClient
    market_data_collector: MarketDataCollector
    order_engine: OrderEngine
    fail_safe_monitor: FailSafeMonitor
    reconnect_interval_seconds: float = 5.0
    fallback_reconcile_interval_seconds: float = 10.0
    _next_reconnect_at: float = 0.0
    _last_fallback_reconcile_at: float = 0.0

    def start(self) -> None:
        self._connect_and_subscribe()

    def stop(self) -> None:
        self.kis_ws_client.disconnect()

    def drain_once(self) -> None:
        now = monotonic()
        if self.fail_safe_monitor.should_use_rest_fallback():
            self._run_rest_fallback(now)
            self._attempt_reconnect(now)
            return

        try:
            events = self.kis_ws_client.poll_events()
            if events:
                self.fail_safe_monitor.record_heartbeat("kis_ws")
            for event in events:
                if event.event_type == "quote":
                    continue
                self.order_engine.handle_broker_event(event)
        except Exception:
            self.fail_safe_monitor.on_stream_disconnect("kis_ws")
            self.kis_ws_client.disconnect()
            self._run_rest_fallback(now)
            self._attempt_reconnect(now)

    def _connect_and_subscribe(self) -> None:
        try:
            self.kis_ws_client.connect()
            self.kis_ws_client.subscribe_order_events()
            self.fail_safe_monitor.on_stream_recovered("kis_ws")
        except Exception:
            self.fail_safe_monitor.on_stream_disconnect("kis_ws")
            self._next_reconnect_at = monotonic() + self.reconnect_interval_seconds

    def _attempt_reconnect(self, now: float) -> None:
        if now < self._next_reconnect_at:
            return
        self._connect_and_subscribe()
        self._next_reconnect_at = now + self.reconnect_interval_seconds

    def _run_rest_fallback(self, now: float) -> None:
        if now - self._last_fallback_reconcile_at < self.fallback_reconcile_interval_seconds:
            return
        self.order_engine.reconcile_unknown_orders()
        self._last_fallback_reconcile_at = now
