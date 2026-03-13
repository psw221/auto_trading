from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from auto_trading.app.runner import ApplicationRunner


@dataclass(slots=True)
class _StubRuntime:
    started: int = 0
    drained: int = 0
    stopped: int = 0

    def start(self) -> None:
        self.started += 1

    def drain_once(self) -> None:
        self.drained += 1

    def stop(self) -> None:
        self.stopped += 1


@dataclass(slots=True)
class _StubScheduler:
    loop_sleep_seconds: float = 0.25
    ticked: int = 0

    def tick(self) -> None:
        self.ticked += 1


@dataclass(slots=True)
class _StubRecovery:
    recovered: int = 0

    def recover(self) -> None:
        self.recovered += 1


@dataclass(slots=True)
class _StubNotifier:
    events: list[dict[str, str]] = field(default_factory=list)

    def send_system_event(self, payload: dict[str, str]) -> None:
        self.events.append(payload)


@dataclass(slots=True)
class _StubContainer:
    runtime: _StubRuntime = field(default_factory=_StubRuntime)
    scheduler: _StubScheduler = field(default_factory=_StubScheduler)
    recovery_service: _StubRecovery = field(default_factory=_StubRecovery)
    notifier: _StubNotifier = field(default_factory=_StubNotifier)


class ApplicationRunnerTest(unittest.TestCase):
    def test_run_once_performs_startup_recovery_and_tick(self) -> None:
        container = _StubContainer()
        runner = ApplicationRunner(container=container, loop_sleep_seconds=0.01)
        runner.run_once()
        self.assertEqual(1, container.recovery_service.recovered)
        self.assertEqual(1, container.runtime.started)
        self.assertEqual(1, container.scheduler.ticked)
        self.assertEqual(1, container.runtime.drained)
        self.assertEqual("auto_trading started", container.notifier.events[-1]["message"])

    def test_run_once_skips_recovery_when_disabled(self) -> None:
        container = _StubContainer()
        runner = ApplicationRunner(
            container=container,
            loop_sleep_seconds=0.01,
            perform_startup_recovery=False,
        )
        runner.run_once()
        self.assertEqual(0, container.recovery_service.recovered)
        self.assertEqual(1, container.runtime.started)
        self.assertEqual(1, container.scheduler.ticked)
        self.assertEqual(1, container.runtime.drained)

    def test_stop_calls_runtime_stop_after_start(self) -> None:
        container = _StubContainer()
        runner = ApplicationRunner(container=container, loop_sleep_seconds=0.01)
        runner.start()
        runner.stop()
        self.assertEqual(1, container.runtime.stopped)
        self.assertEqual("auto_trading stopped", container.notifier.events[-1]["message"])


if __name__ == "__main__":
    unittest.main()
