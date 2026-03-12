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
        self._started = True

    def run_once(self) -> None:
        if not self._started:
            self.start()
        self.container.scheduler.tick()
        self.container.runtime.drain_once()

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
        self._started = False
