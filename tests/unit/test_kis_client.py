from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib import error

from auto_trading.broker.dto import BrokerOrderRequest
from auto_trading.broker.kis_client import KISClient
from auto_trading.common.exceptions import BrokerApiError, BrokerResponseError
from auto_trading.config.schema import Settings


class StubKISClient(KISClient):
    def __init__(self, settings: Settings, payloads: list[dict[str, object]]):
        super().__init__(settings)
        self.payloads = payloads

    def _request_json(self, **kwargs):  # type: ignore[override]
        return self.payloads.pop(0)


def build_settings() -> Settings:
    return Settings(
        env="demo",
        db_path=Path("data/test.db"),
        kis_base_url="https://example.com",
        kis_ws_url="ws://example.com",
        kis_app_key="key",
        kis_app_secret="secret",
        kis_cano="123",
        kis_acnt_prdt_cd="01",
        kis_access_token="token",
        kis_refresh_token="",
        kis_user_id="user1",
        universe_master_path=Path("data/universe_master.csv"),
        holiday_calendar_path=Path("data/krx_holidays.csv"),
        holiday_api_service_key="",
        telegram_bot_token="",
        telegram_chat_id="",
    )


class KISClientTest(unittest.TestCase):
    def test_place_cash_order_maps_response(self) -> None:
        client = StubKISClient(
            build_settings(),
            [{"rt_cd": "0", "msg_cd": "0", "msg1": "ok", "output": {"ODNO": "12345"}}],
        )
        response = client.place_cash_order(BrokerOrderRequest(symbol="005930", side="BUY", qty=1, order_type="LIMIT", price=70000))
        self.assertTrue(response.accepted)
        self.assertEqual("12345", response.order_no)

    def test_get_balance_parses_output2(self) -> None:
        client = StubKISClient(
            build_settings(),
            [{"output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "1200000"}]}],
        )
        balance = client.get_balance()
        self.assertEqual(1000000.0, balance.cash)
        self.assertEqual(1200000.0, balance.total_asset)

    def test_ensure_access_token_issues_new_token_when_missing(self) -> None:
        settings = build_settings()
        settings.kis_access_token = ""
        client = StubKISClient(settings, [{"access_token": "issued-token"}])
        token = client._ensure_access_token()
        self.assertEqual("issued-token", token)

    @patch("urllib.request.urlopen")
    def test_request_json_raises_for_invalid_json(self, mock_urlopen: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = b"not-json"
        response.__enter__.return_value = response
        mock_urlopen.return_value = response
        client = KISClient(build_settings())
        with self.assertRaises(BrokerResponseError):
            client._request_json(method="GET", path="/test")

    @patch("urllib.request.urlopen")
    def test_request_json_raises_for_url_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = error.URLError("boom")
        client = KISClient(build_settings())
        with self.assertRaises(BrokerApiError):
            client._request_json(method="GET", path="/test")


if __name__ == "__main__":
    unittest.main()
