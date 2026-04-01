from __future__ import annotations

from dataclasses import dataclass
from time import sleep


@dataclass(slots=True)
class ApplicationRunner:
    container: object
    loop_sleep_seconds: float = 1.0
    perform_startup_recovery: bool = True
    _started: bool = False

    def start(self) -> None:
        if self._started:
            return
        if self.perform_startup_recovery:
            self.container.recovery_service.recover()
        self.container.runtime.start()
        self._notify_system_event(
            message="auto_trading started",
            severity="INFO",
            component="runner",
        )
        self._started = True

    def run_once(self) -> None:
        if not self._started:
            self.start()
        self.container.scheduler.tick()
        self.container.runtime.drain_once()
        telegram_command_service = getattr(self.container, 'telegram_command_service', None)
        if telegram_command_service is not None:
            telegram_command_service.poll_once()

    def run_forever(self) -> None:
        if not self._started:
            self.start()
        while True:
            self.run_once()
            sleep(self.loop_sleep_seconds)

    def stop(self) -> None:
        if not self._started:
            return
        self.container.runtime.stop()
        self._notify_system_event(
            message="auto_trading stopped",
            severity="INFO",
            component="runner",
        )
        self._started = False

    def _notify_system_event(self, *, message: str, severity: str, component: str) -> None:
        notifier = getattr(self.container, "notifier", None)
        if notifier is None:
            return
        notifier.send_system_event(
            {
                "message": message,
                "severity": severity,
                "component": component,
            }
        )
