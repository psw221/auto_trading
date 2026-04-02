from __future__ import annotations

import csv
import json
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, parse, request

try:
    import certifi
except ModuleNotFoundError:
    certifi = None

from auto_trading.config.schema import Settings


def _build_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


@dataclass(slots=True)
class TelegramNotifier:
    settings: Settings
    system_events_repository: object
    timeout: float = 5.0
    ssl_context: ssl.SSLContext = field(default_factory=lambda: _build_ssl_context())
    _symbol_name_cache: dict[str, str] = field(default_factory=dict)
    _symbol_name_cache_loaded: bool = False

    def send_trade_fill(self, payload: object) -> None:
        if not getattr(self.settings, 'telegram_notify_trade_fill', True):
            return
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        message = self._format_trade_fill_message(normalized)
        self._send_message(
            message=message,
            event_type="trade_fill_notification",
            payload=normalized,
        )

    def send_target_scores(self, payload: object) -> None:
        if not getattr(self.settings, 'telegram_notify_target_scores', True):
            return
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        items = normalized.get("items")
        if not isinstance(items, list) or not items:
            return
        filtered_items = [
            item for item in items
            if isinstance(item, dict) and self._score_total(item) >= 70
        ]
        if not filtered_items:
            return
        normalized = dict(normalized)
        normalized["items"] = filtered_items
        message = self._format_target_scores_message(normalized)
        if not message:
            return
        self._send_message(
            message=message,
            event_type="target_scores_notification",
            payload=normalized,
        )

    def send_system_event(self, payload: object) -> None:
        if not getattr(self.settings, 'telegram_notify_system_event', True):
            return
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        message = self._format_system_event_message(normalized)
        self._send_message(
            message=message,
            event_type="notification",
            payload=normalized,
        )

    def send_trade_recovery(self, payload: object) -> None:
        if not getattr(self.settings, 'telegram_notify_trade_recovery', True):
            return
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        message = self._format_trade_recovery_message(normalized)
        self._send_message(
            message=message,
            event_type="trade_recovery_notification",
            payload=normalized,
        )

    def send_daily_report(self, payload: object) -> None:
        if not getattr(self.settings, 'telegram_notify_daily_report', True):
            return
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        message = self._format_daily_report_message(normalized)
        self._send_message(
            message=message,
            event_type="daily_report_notification",
            payload=normalized,
        )


    def send_command_response(self, payload: object) -> None:
        normalized = payload if isinstance(payload, dict) else {"payload": str(payload)}
        message = str(normalized.get("message", "")).strip()
        if not message:
            return
        self._send_message(
            message=message,
            event_type="command_response_notification",
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
            with request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as response:
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

    def _format_trade_recovery_message(self, payload: dict[str, object]) -> str:
        symbol = str(payload.get("symbol", ""))
        symbol_name = self._resolve_symbol_name(payload)
        side = str(payload.get("side", ""))
        side_label = self._format_side(side)
        reason_label = self._format_reason(str(payload.get("reason", "")), side)
        qty = self._format_qty(payload.get("qty", ""))
        price = self._format_price(payload.get("price", ""))
        source = str(payload.get("source", ""))
        broker_order_id = str(payload.get("broker_order_id", ""))

        symbol_line = symbol
        if symbol_name and symbol_name != symbol:
            symbol_line = f"{symbol_name} ({symbol})"

        estimated = bool(payload.get('estimated', False))
        filled_at = str(payload.get('filled_at', '')).strip()

        lines = [
            f"[AUTO_TRADING] {side_label} {'추정 ' if estimated else ''}체결 복구",
            f"종목: {symbol_line}",
        ]
        if qty:
            lines.append(f"수량: {qty}주")
        if price and price != '-':
            lines.append(f"기준 가격: {price}원")
        lines.append(f"사유: {reason_label}")
        if filled_at:
            lines.append(f"기준 시각: {filled_at}")
        if source:
            lines.append(f"복구 근거: {source}")
        if estimated:
            lines.append("주의: 실제 체결가/체결시각은 브로커 체결내역 확인 전까지 추정값입니다.")
        if broker_order_id:
            lines.append(f"주문번호: {broker_order_id}")
        return "\n".join(lines)

    def _format_trade_fill_message(self, payload: dict[str, object]) -> str:
        symbol = str(payload.get("symbol", ""))
        symbol_name = self._resolve_symbol_name(payload)
        side = str(payload.get("side", ""))
        side_label = self._format_side(side)
        reason_label = self._format_reason(str(payload.get("reason", "")), side)
        fill_qty = self._format_qty(payload.get("fill_qty", ""))
        fill_price = self._format_price(payload.get("fill_price", ""))
        filled_at = str(payload.get("filled_at", ""))
        filled_qty = self._format_qty(payload.get("filled_qty", ""))
        total_qty = self._format_qty(payload.get("total_qty", ""))
        remaining_qty = self._format_qty(payload.get("remaining_qty", ""))
        position_qty = self._format_qty(payload.get("position_qty", ""))

        symbol_line = symbol
        if symbol_name and symbol_name != symbol:
            symbol_line = f"{symbol_name} ({symbol})"

        lines = [
            f"[AUTO_TRADING] {side_label} 체결",
            f"종목: {symbol_line}",
            f"사유: {reason_label}",
            f"체결: {fill_qty}주 @ {fill_price}원",
        ]
        if filled_qty and total_qty:
            lines.append(f"주문 진행: {filled_qty}/{total_qty}주 체결")
        if remaining_qty:
            lines.append(f"미체결 잔량: {remaining_qty}주")
        if position_qty:
            lines.append(f"현재 보유: {position_qty}주")
        if filled_at:
            lines.append(f"체결 시각: {filled_at}")
        return "\n".join(lines)

    def _format_target_scores_message(self, payload: dict[str, object]) -> str:
        snapshot_time = str(payload.get("snapshot_time", ""))
        items = payload.get("items", [])
        qualifying_items = [
            item for item in items
            if isinstance(item, dict) and self._score_total(item) >= 70
        ]
        if not qualifying_items:
            return ""
        lines = ["[AUTO_TRADING] 타겟 점수 TOP 10"]
        if snapshot_time:
            lines.append(f"기준 시각: {snapshot_time}")
        for index, item in enumerate(qualifying_items, start=1):
            symbol = str(item.get("symbol", ""))
            symbol_name = self._resolve_symbol_name(item)
            display_name = symbol if not symbol_name or symbol_name == symbol else f"{symbol_name} ({symbol})"
            score_total = item.get("score_total", "")
            price = self._format_price(item.get("price", ""))
            lines.append(f"{index}. {display_name} | 점수 {score_total} | 현재가 {price}원")
        return "\n".join(lines)

    @staticmethod
    def _score_total(payload: dict[str, object]) -> int:
        try:
            return int(float(str(payload.get("score_total", 0))))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _format_daily_report_message(payload: dict[str, object]) -> str:
        return str(payload.get("message", "")).strip() or "[AUTO_TRADING] 일일 리포트"

    @staticmethod
    def _format_system_event_message(payload: dict[str, object]) -> str:
        message = str(payload.get("message", "system event"))
        component = str(payload.get("component", "system"))
        severity = str(payload.get("severity", "INFO"))
        return (
            "[AUTO_TRADING] 시스템 알림\n"
            f"등급: {severity}\n"
            f"영역: {component}\n"
            f"내용: {message}"
        )

    def _resolve_symbol_name(self, payload: dict[str, object]) -> str:
        explicit_name = str(payload.get("symbol_name", "")).strip()
        if explicit_name:
            return explicit_name
        symbol = str(payload.get("symbol", "")).strip()
        if not symbol:
            return ""
        if not self._symbol_name_cache_loaded:
            self._load_symbol_name_cache()
        return self._symbol_name_cache.get(symbol, "")

    def _load_symbol_name_cache(self) -> None:
        self._symbol_name_cache_loaded = True
        path = Path(self.settings.universe_master_path)
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    symbol = str(row.get("symbol", "")).strip()
                    name = str(row.get("name", "")).strip()
                    if symbol and name:
                        self._symbol_name_cache[symbol] = name
        except OSError:
            return

    @staticmethod
    def _format_side(side: str) -> str:
        return {"BUY": "매수", "SELL": "매도"}.get(side.upper(), side or "체결")

    @staticmethod
    def _format_reason(reason: str, side: str) -> str:
        normalized = reason.upper()
        mapping = {
            "ENTRY": "전략 진입",
            "EXIT": "일반 청산",
            "STOPLOSS": "손절",
            "TAKEPROFIT": "익절",
            "TIMEEXIT": "보유 기간 종료",
            "EODPROFITLOCK": "장마감 수익 보호 청산",
            "EOD_PROFIT_LOCK": "장마감 수익 보호 청산",
            "REPLACE": "정정 주문",
            "CANCEL": "주문 취소",
        }
        if normalized in mapping:
            return mapping[normalized]
        if side.upper() == "BUY":
            return "매수 체결"
        if side.upper() == "SELL":
            return "매도 체결"
        return reason or "체결"

    @staticmethod
    def _format_price(value: object) -> str:
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            text = str(value).strip()
            return text or "-"

    @staticmethod
    def _format_qty(value: object) -> str:
        if value in (None, ""):
            return ""
        try:
            return f"{int(float(str(value))):,}"
        except (TypeError, ValueError):
            return str(value).strip()

