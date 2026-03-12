from __future__ import annotations

import argparse

from auto_trading.app.bootstrap import bootstrap
from auto_trading.app.runner import ApplicationRunner


def main() -> None:
    args = _parse_args()
    container = bootstrap()
    runner = ApplicationRunner(
        container=container,
        loop_sleep_seconds=container.scheduler.loop_sleep_seconds,
        perform_startup_recovery=not args.no_startup_recovery,
    )
    try:
        if args.once:
            runner.run_once()
            return
        runner.run_forever()
    except KeyboardInterrupt:
        runner.stop()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto trading runtime entrypoint")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scheduler/runtime cycle and exit.",
    )
    parser.add_argument(
        "--no-startup-recovery",
        action="store_true",
        help="Skip broker recovery during process startup.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
