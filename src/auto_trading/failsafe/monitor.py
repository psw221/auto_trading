from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass(slots=True)
class FailSafeMonitor:
    blocked: bool = False
    heartbeats: dict[str, float] = field(default_factory=dict)
    stream_failures: dict[str, int] = field(default_factory=dict)
    fallback_active: bool = False

    def record_heartbeat(self, component: str) -> None:
        self.heartbeats[component] = monotonic()

    def on_api_error(self, error: Exception) -> None:
        self.blocked = True

    def on_stream_disconnect(self, stream_name: str) -> None:
        self.blocked = True
        self.fallback_active = True
        self.stream_failures[stream_name] = self.stream_failures.get(stream_name, 0) + 1

    def on_stream_recovered(self, stream_name: str) -> None:
        self.record_heartbeat(stream_name)
        self.fallback_active = False
        self.blocked = False

    def should_block_new_orders(self) -> bool:
        return self.blocked

    def should_use_rest_fallback(self) -> bool:
        return self.fallback_active

    def time_since_heartbeat(self, component: str) -> float | None:
        last = self.heartbeats.get(component)
        if last is None:
            return None
        return monotonic() - last
