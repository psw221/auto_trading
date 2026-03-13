from __future__ import annotations

from auto_trading.app.dashboard import build_dashboard_summary, format_dashboard_summary
from auto_trading.config.settings import load_settings


def main() -> None:
    settings = load_settings()
    summary = build_dashboard_summary(settings.db_path, settings.universe_master_path)
    print(format_dashboard_summary(summary, settings.db_path))


if __name__ == "__main__":
    main()
