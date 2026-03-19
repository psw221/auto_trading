from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from auto_trading.app.runtime import RuntimeService
from auto_trading.broker.dto import BrokerRealtimeEvent


@dataclass(slots=True)
class _StubWSClient:
    events: list[BrokerRealtimeEvent] = field(default_factory=list)
    connected: int = 0
    disconnected: int = 0
    order_subscribed: int = 0
    quote_subscribed: int = 0

    def connect(self) -> None:
        self.connected += 1

    def disconnect(self) -> None:
        self.disconnected += 1

    def subscribe_order_events(self) -> None:
        self.order_subscribed += 1

    def subscribe_quotes(self, symbols: list[str]) -> None:
        self.quote_subscribed += 1

    def poll_events(self) -> list[BrokerRealtimeEvent]:
        return list(self.events)


@dataclass(slots=True)
class _StubCollector:
    updated: int = 0

    def update_quote(self, event: BrokerRealtimeEvent) -> None:
        self.updated += 1


@dataclass(slots=True)
class _StubOrderEngine:
    handled: list[BrokerRealtimeEvent] = field(default_factory=list)
    reconciled: int = 0

    def handle_broker_event(self, event: BrokerRealtimeEvent) -> None:
        self.handled.append(event)

    def reconcile_unknown_orders(self) -> None:
        self.reconciled += 1


@dataclass(slots=True)
class _StubFailSafeMonitor:
    heartbeats: list[str] = field(default_factory=list)
    disconnected: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    use_rest_fallback: bool = False

    def should_use_rest_fallback(self) -> bool:
        return self.use_rest_fallback

    def record_heartbeat(self, name: str) -> None:
        self.heartbeats.append(name)

    def on_stream_disconnect(self, name: str) -> None:
        self.disconnected.append(name)

    def on_stream_recovered(self, name: str) -> None:
        self.recovered.append(name)


class RuntimeServiceTest(unittest.TestCase):
    def test_start_subscribes_order_events_only(self) -> None:
        ws_client = _StubWSClient()
        runtime = RuntimeService(
            kis_ws_client=ws_client,
            market_data_collector=_StubCollector(),
            order_engine=_StubOrderEngine(),
            fail_safe_monitor=_StubFailSafeMonitor(),
        )
        runtime.start()
        self.assertEqual(1, ws_client.connected)
        self.assertEqual(1, ws_client.order_subscribed)
        self.assertEqual(0, ws_client.quote_subscribed)

    def test_drain_once_ignores_quote_events(self) -> None:
        ws_client = _StubWSClient(
            events=[
                BrokerRealtimeEvent(event_type='quote', symbol='005930', payload={'price': '70000'}),
                BrokerRealtimeEvent(event_type='fill', symbol='005930', payload={'order_no': '1'}),
            ]
        )
        collector = _StubCollector()
        order_engine = _StubOrderEngine()
        runtime = RuntimeService(
            kis_ws_client=ws_client,
            market_data_collector=collector,
            order_engine=order_engine,
            fail_safe_monitor=_StubFailSafeMonitor(),
        )
        runtime.drain_once()
        self.assertEqual(0, collector.updated)
        self.assertEqual(1, len(order_engine.handled))
        self.assertEqual('fill', order_engine.handled[0].event_type)


if __name__ == '__main__':
    unittest.main()
