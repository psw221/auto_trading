from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, parse, request

from auto_trading.config.schema import Settings


@dataclass(slots=True)
class TelegramNotifier:
    settings: Settings
    system_events_repository: object
    timeout: float = 5.0

    def send_trade_fill(self, payload: object) -> None:
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        message = self._format_trade_fill_message(normalized)
        self._send_message(
            message=message,
            event_type="trade_fill_notification",
            payload=normalized,
        )

    def send_system_event(self, payload: object) -> None:
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        message = self._format_system_event_message(normalized)
        self._send_message(
            message=message,
            event_type="notification",
            payload=normalized,
        )

    def _send_message(self, *, message: str, event_type: str, payload: dict[str, object]) -> None:
        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            self.system_events_repository.create(
                event_type=f"{event_type}_skipped",
                severity="WARN",
                component="telegram",
                message="Telegram credentials are not configured.",
                payload=payload,
            )
            return

        body = parse.urlencode(
            {
                "chat_id": self.settings.telegram_chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = request.Request(
            url=self._build_send_message_url(),
            data=body,
            headers={"content-type": "application/x-www-form-urlencoded; charset=utf-8"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                response_text = response.read().decode("utf-8")
                parsed = json.loads(response_text)
                if not parsed.get("ok", False):
                    raise ValueError(parsed.get("description", "Telegram API returned an error."))
        except (error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.system_events_repository.create(
                event_type=f"{event_type}_failed",
                severity="ERROR",
                component="telegram",
                message=f"Telegram delivery failed: {exc}",
                payload=payload,
            )
            return

        self.system_events_repository.create(
            event_type=f"{event_type}_sent",
            severity="INFO",
            component="telegram",
            message=message,
            payload=payload,
        )

    def _build_send_message_url(self) -> str:
        return f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"

    @staticmethod
    def _format_trade_fill_message(payload: dict[str, object]) -> str:
        symbol = str(payload.get("symbol", ""))
        side = str(payload.get("side", ""))
        fill_qty = payload.get("fill_qty", "")
        fill_price = payload.get("fill_price", "")
        filled_at = str(payload.get("filled_at", ""))
        return (
            "[AUTO_TRADING] Trade Fill\n"
            f"symbol={symbol}\n"
            f"side={side}\n"
            f"qty={fill_qty}\n"
            f"price={fill_price}\n"
            f"filled_at={filled_at}"
        )

    @staticmethod
    def _format_system_event_message(payload: dict[str, object]) -> str:
        message = str(payload.get("message", "system event"))
        component = str(payload.get("component", "system"))
        severity = str(payload.get("severity", "INFO"))
        return (
            "[AUTO_TRADING] System Event\n"
            f"severity={severity}\n"
            f"component={component}\n"
            f"message={message}"
        )
