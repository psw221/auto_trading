from __future__ import annotations

import json
from base64 import b64decode
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from auto_trading.broker.dto import BrokerRealtimeEvent
from auto_trading.config.schema import Settings

try:
    import websocket
except ImportError:  # pragma: no cover - optional runtime dependency
    websocket = None

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except ImportError:  # pragma: no cover - optional runtime dependency
    AES = None
    unpad = None


ORDER_NOTICE_COLUMNS = [
    "CUST_ID",
    "ACNT_NO",
    "ODER_NO",
    "OODER_NO",
    "SELN_BYOV_CLS",
    "RCTF_CLS",
    "ODER_KIND",
    "ODER_COND",
    "STCK_SHRN_ISCD",
    "CNTG_QTY",
    "CNTG_UNPR",
    "STCK_CNTG_HOUR",
    "RFUS_YN",
    "CNTG_YN",
    "ACPT_YN",
    "BRNC_NO",
    "ODER_QTY",
    "ACNT_NAME",
    "ORD_COND_PRC",
    "ORD_EXG_GB",
    "POPUP_YN",
    "FILLER",
    "CRDT_CLS",
    "CRDT_LOAN_DATE",
    "CNTG_ISNM40",
    "ODER_PRC",
]

TRADE_TICK_COLUMNS = [
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
    "PRDY_VRSS_SIGN",
    "PRDY_VRSS",
    "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC",
    "STCK_OPRC",
    "STCK_HGPR",
    "STCK_LWPR",
    "ASKP1",
    "BIDP1",
    "CNTG_VOL",
    "ACML_VOL",
    "ACML_TR_PBMN",
    "SELN_CNTG_CSNU",
    "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU",
    "CTTR",
    "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN",
    "CCLD_DVSN",
    "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR",
    "HGPR_HOUR",
    "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR",
    "LWPR_HOUR",
    "LWPR_VRSS_PRPR_SIGN",
    "LWPR_VRSS_PRPR",
    "BSOP_DATE",
    "NEW_MKOP_CLS_CODE",
    "TRHT_YN",
    "ASKP_RSQN1",
    "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL",
    "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE",
    "VI_STND_PRC",
]

@dataclass(slots=True)
class KISWebSocketClient:
    settings: Settings
    kis_client: object
    subscribed_symbols: list[str] = field(default_factory=list)
    _approval_key: str = field(init=False, default="")
    _socket: object | None = field(init=False, default=None)
    _pending_events: deque[BrokerRealtimeEvent] = field(init=False, default_factory=deque)
    _aes_context_by_trid: dict[str, tuple[str, str]] = field(init=False, default_factory=dict)
    _active_quote_subscriptions: set[str] = field(init=False, default_factory=set)

    def connect(self) -> None:
        self._approval_key = self.kis_client.get_approval_key()
        self._active_quote_subscriptions.clear()
        if websocket is None:
            return None
        self._socket = websocket.create_connection(self.settings.kis_ws_url, timeout=1)

    def subscribe_quotes(self, symbols: list[str]) -> None:
        normalized: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(symbol)
        self.subscribed_symbols = normalized
        for symbol in normalized:
            if symbol in self._active_quote_subscriptions:
                continue
            self._send_subscription(self._quote_tr_id(), symbol)
            self._active_quote_subscriptions.add(symbol)

    def subscribe_order_events(self) -> None:
        self._send_subscription(self._order_event_tr_id(), self.settings.kis_user_id or "ORDER")

    def poll_events(self) -> list[BrokerRealtimeEvent]:
        self._drain_socket()
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def disconnect(self) -> None:
        self._active_quote_subscriptions.clear()
        if self._socket is None:
            return None
        try:
            self._socket.close()
        finally:
            self._socket = None

    def is_connected(self) -> bool:
        return self._socket is not None or websocket is None

    def feed_mock_message(self, raw_message: str | dict[str, Any]) -> None:
        event = self._parse_message(raw_message)
        if event is not None:
            self._pending_events.append(event)

    def register_aes_context(self, tr_id: str, key: str, iv: str) -> None:
        self._aes_context_by_trid[tr_id] = (key, iv)

    def _send_subscription(self, tr_id: str, tr_key: str) -> None:
        if not self._approval_key or self._socket is None:
            return None
        payload = {
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key,
                }
            },
        }
        self._socket.send(json.dumps(payload))

    def _drain_socket(self) -> None:
        if self._socket is None or websocket is None:
            return None
        self._socket.settimeout(0.1)
        while True:
            try:
                raw_message = self._socket.recv()
            except Exception:
                break
            event = self._parse_message(raw_message)
            if event is not None:
                self._pending_events.append(event)

    def _parse_message(self, raw_message: str | dict[str, Any]) -> BrokerRealtimeEvent | None:
        if isinstance(raw_message, dict):
            return self._parse_json_message(raw_message)
        message = raw_message.strip()
        if not message:
            return None
        if message.startswith("{"):
            return self._parse_json_message(json.loads(message))
        if "|" in message:
            return self._parse_pipe_message(message)
        return None

    def _parse_json_message(self, payload: dict[str, Any]) -> BrokerRealtimeEvent | None:
        header = payload.get("header", {})
        body = payload.get("body", {})
        tr_id = str(header.get("tr_id", ""))
        if "output" in body and isinstance(body["output"], dict):
            output_dict = body["output"]
            if "key" in output_dict and "iv" in output_dict:
                self.register_aes_context(tr_id, str(output_dict["key"]), str(output_dict["iv"]))
                return None
        if tr_id in {self._order_event_tr_id()}:
            output = body.get("output", body.get("output1", body))
            if isinstance(output, dict):
                cntg_yn = str(output.get("CNTG_YN", output.get("cntg_yn", "")))
                rfus_yn = str(output.get("RFUS_YN", output.get("rfus_yn", "")))
                acpt_yn = str(output.get("ACPT_YN", output.get("acpt_yn", "")))
                event_type = "fill" if cntg_yn == "2" else "order"
                return BrokerRealtimeEvent(
                    event_type=event_type,
                    symbol=output.get("PDNO", output.get("pdno")),
                    payload={
                        "order_no": str(output.get("ODNO", output.get("odno", ""))),
                        "symbol": str(output.get("PDNO", output.get("pdno", ""))),
                        "side": self._normalize_side(output.get("SLL_BUY_DVSN_CD", output.get("sll_buy_dvsn_cd", ""))),
                        "fill_qty": str(output.get("CNTG_QTY", output.get("cntg_qty", "0"))),
                        "fill_price": str(output.get("CNTG_UNPR", output.get("cntg_unpr", "0"))),
                        "filled_at": str(output.get("ORD_TMD", output.get("ord_tmd", ""))),
                        "status": self._resolve_notice_status(cntg_yn, rfus_yn, acpt_yn),
                        "message": str(output.get("MSG1", output.get("msg1", ""))),
                    },
                )
        if tr_id == self._quote_tr_id():
            output = body.get("output", body)
            if isinstance(output, dict):
                symbol = str(output.get("PDNO", output.get("pdno", "")))
                price = str(output.get("STCK_PRPR", output.get("stck_prpr", "0")))
                return BrokerRealtimeEvent(
                    event_type="quote",
                    symbol=symbol,
                    payload={"price": price},
                )
        return None

    def _parse_pipe_message(self, message: str) -> BrokerRealtimeEvent | None:
        parts = message.split("|")
        if len(parts) < 4:
            return None
        tr_id = parts[1]
        raw_payload = parts[-1]
        if tr_id == self._order_event_tr_id() and tr_id in self._aes_context_by_trid:
            raw_payload = self._decrypt_notice_payload(tr_id, raw_payload)
        fields = raw_payload.split("^")
        if tr_id == self._order_event_tr_id():
            payload_map = self._map_fields(ORDER_NOTICE_COLUMNS, fields)
            cntg_yn = payload_map.get("CNTG_YN", "")
            rfus_yn = payload_map.get("RFUS_YN", "")
            acpt_yn = payload_map.get("ACPT_YN", "")
            event_type = "fill" if cntg_yn == "2" else "order"
            payload = {
                "order_no": payload_map.get("ODER_NO", ""),
                "symbol": payload_map.get("STCK_SHRN_ISCD", ""),
                "side": self._normalize_side(payload_map.get("SELN_BYOV_CLS", "")),
                "fill_qty": payload_map.get("CNTG_QTY", "0"),
                "fill_price": payload_map.get("CNTG_UNPR", "0"),
                "filled_at": payload_map.get("STCK_CNTG_HOUR", ""),
                "status": self._resolve_notice_status(cntg_yn, rfus_yn, acpt_yn),
                "message": "",
            }
            return BrokerRealtimeEvent(event_type=event_type, symbol=payload["symbol"], payload=payload)
        if tr_id == self._quote_tr_id():
            payload_map = self._map_fields(TRADE_TICK_COLUMNS, fields)
            symbol = payload_map.get("MKSC_SHRN_ISCD", "")
            price = payload_map.get("STCK_PRPR", "0")
            return BrokerRealtimeEvent(event_type="quote", symbol=symbol, payload={"price": price})
        return None

    def _order_event_tr_id(self) -> str:
        return "H0STCNI0" if self.settings.env == "real" else "H0STCNI9"

    def _quote_tr_id(self) -> str:
        return "H0STCNT0" if self.settings.env == "real" else "H0STCNT0"

    @staticmethod
    def _pick_field(fields: list[str], index: int, default: str = "") -> str:
        if index >= len(fields):
            return default
        return fields[index]

    @staticmethod
    def _map_fields(columns: list[str], fields: list[str]) -> dict[str, str]:
        return {column: fields[index] if index < len(fields) else "" for index, column in enumerate(columns)}

    @staticmethod
    def _normalize_side(value: Any) -> str:
        text = str(value)
        if text in {"01", "1", "SELL"}:
            return "SELL"
        if text in {"02", "2", "BUY"}:
            return "BUY"
        return text

    @staticmethod
    def _resolve_notice_status(cntg_yn: str, rfus_yn: str, acpt_yn: str) -> str:
        cntg_value = str(cntg_yn).strip().upper()
        refuse_value = str(rfus_yn).strip().upper()
        accept_value = str(acpt_yn).strip().upper()
        if cntg_value == "2":
            return "FILLED"
        if refuse_value in {"Y", "1"}:
            return "REJECTED"
        if accept_value in {"Y", "1"} or cntg_value == "1":
            return "ACKNOWLEDGED"
        return "UNKNOWN"

    def _decrypt_notice_payload(self, tr_id: str, raw_payload: str) -> str:
        if AES is None or unpad is None:
            return raw_payload
        key, iv = self._aes_context_by_trid[tr_id]
        try:
            cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
            decrypted = cipher.decrypt(b64decode(raw_payload))
            return unpad(decrypted, AES.block_size).decode("utf-8")
        except Exception:
            return raw_payload
