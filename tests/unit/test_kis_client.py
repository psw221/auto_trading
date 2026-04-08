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
        rest_min_interval_seconds=0.12,
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

    def test_get_daily_bars_parses_ohlcv_output2(self) -> None:
        client = StubKISClient(
            build_settings(),
            [{
                'output2': [
                    {
                        'stck_oprc': '70000',
                        'stck_hgpr': '71000',
                        'stck_lwpr': '69000',
                        'stck_clpr': '70500',
                        'acml_vol': '123456',
                        'acml_tr_pbmn': '10000000000',
                    }
                ]
            }],
        )
        bars = client.get_daily_bars('005930', lookback_days=1)
        self.assertEqual(1, len(bars))
        self.assertEqual(70000.0, bars[0]['open'])
        self.assertEqual(71000.0, bars[0]['high'])
        self.assertEqual(69000.0, bars[0]['low'])
        self.assertEqual(70500.0, bars[0]['close'])
        self.assertEqual(123456.0, bars[0]['volume'])
        self.assertEqual(10000000000.0, bars[0]['turnover'])

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
    def test_request_json_retries_once_when_token_is_expired(self, mock_urlopen: MagicMock) -> None:
        expired_response = MagicMock()
        expired_response.read.return_value = json.dumps({
            "rt_cd": "1",
            "msg1": "기간이 만료된 token 입니다.",
        }).encode("utf-8")
        expired_response.__enter__.return_value = expired_response

        issue_token_response = MagicMock()
        issue_token_response.read.return_value = json.dumps({
            "access_token": "fresh-token"
        }).encode("utf-8")
        issue_token_response.__enter__.return_value = issue_token_response

        success_response = MagicMock()
        success_response.read.return_value = json.dumps({
            "rt_cd": "0",
            "output": {"value": 1}
        }).encode("utf-8")
        success_response.__enter__.return_value = success_response

        mock_urlopen.side_effect = [expired_response, issue_token_response, success_response]

        settings = build_settings()
        settings.kis_access_token = "stale-token"
        client = KISClient(settings)

        data = client._request_json(method="GET", path="/test")

        self.assertEqual("0", data["rt_cd"])
        self.assertEqual("fresh-token", client._access_token)
        self.assertEqual(3, mock_urlopen.call_count)
        first_headers = {str(k).lower(): v for k, v in mock_urlopen.call_args_list[0].args[0].header_items()}
        second_headers = {str(k).lower(): v for k, v in mock_urlopen.call_args_list[2].args[0].header_items()}
        self.assertEqual("Bearer stale-token", first_headers.get("authorization"))
        self.assertEqual("Bearer fresh-token", second_headers.get("authorization"))

    def test_should_retry_for_expired_token_message(self) -> None:
        client = KISClient(build_settings())
        self.assertTrue(client._should_retry_for_expired_token({"msg1": "기간이 만료된 token 입니다."}, use_authorization=True, allow_auth_retry=True))
        self.assertFalse(client._should_retry_for_expired_token({"msg1": "ok"}, use_authorization=True, allow_auth_retry=True))
        self.assertFalse(client._should_retry_for_expired_token({"msg1": "기간이 만료된 token 입니다."}, use_authorization=False, allow_auth_retry=True))

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

