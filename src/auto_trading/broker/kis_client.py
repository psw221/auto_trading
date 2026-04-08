from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib import error, parse, request

from auto_trading.broker.dto import (
    BrokerBalance,
    BrokerFillSnapshot,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerPositionSnapshot,
    BrokerOrderSnapshot,
    BrokerReviseCancelRequest,
)
from auto_trading.broker.mapper import resolve_order_tr_id, resolve_revise_cancel_tr_id
from auto_trading.common.exceptions import BrokerApiError, BrokerResponseError
from auto_trading.config.schema import Settings


@dataclass(slots=True)
class KISClient:
    settings: Settings
    timeout: float = 10.0
    _access_token: str = field(init=False, default="")

    def place_cash_order(self, request: BrokerOrderRequest) -> BrokerOrderResponse:
        tr_id = request.tr_id or resolve_order_tr_id(self.settings.env, request.side)
        body = {
            "CANO": self.settings.kis_cano,
            "ACNT_PRDT_CD": self.settings.kis_acnt_prdt_cd,
            "PDNO": request.symbol,
            "ORD_DVSN": self._resolve_order_division(request.order_type),
            "ORD_QTY": str(request.qty),
            "ORD_UNPR": self._format_price(request.price),
        }
        body.update(request.payload)
        data = self._request_json(
            method="POST",
            path="/uapi/domestic-stock/v1/trading/order-cash",
            tr_id=tr_id,
            body=body,
            use_hashkey=True,
        )
        return BrokerOrderResponse(
            order_no=self._extract_order_no(data),
            accepted=data.get("rt_cd") == "0",
            rt_cd=data.get("rt_cd", ""),
            msg_cd=data.get("msg_cd", ""),
            msg=data.get("msg1", ""),
            output=data.get("output", {}) if isinstance(data.get("output", {}), dict) else {},
        )

    def revise_or_cancel_order(self, request: BrokerReviseCancelRequest) -> BrokerOrderResponse:
        body = {
            "CANO": self.settings.kis_cano,
            "ACNT_PRDT_CD": self.settings.kis_acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": request.orig_odno,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "01" if request.mode == "REVISE" else "02",
            "ORD_QTY": str(request.qty),
            "ORD_UNPR": self._format_price(request.price),
            "QTY_ALL_ORD_YN": "N",
        }
        data = self._request_json(
            method="POST",
            path="/uapi/domestic-stock/v1/trading/order-rvsecncl",
            tr_id=resolve_revise_cancel_tr_id(self.settings.env),
            body=body,
            use_hashkey=True,
        )
        return BrokerOrderResponse(
            order_no=self._extract_order_no(data),
            accepted=data.get("rt_cd") == "0",
            rt_cd=data.get("rt_cd", ""),
            msg_cd=data.get("msg_cd", ""),
            msg=data.get("msg1", ""),
            output=data.get("output", {}) if isinstance(data.get("output", {}), dict) else {},
        )

    def get_balance(self) -> BrokerBalance:
        params = {
            "CANO": self.settings.kis_cano,
            "ACNT_PRDT_CD": self.settings.kis_acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id="TTTC8434R" if self.settings.env == "real" else "VTTC8434R",
            params=params,
        )
        output2 = data.get("output2", [])
        if isinstance(output2, list) and output2:
            snapshot = output2[0]
            cash = self._to_float(snapshot.get("dnca_tot_amt"))
            total_asset = self._to_float(snapshot.get("tot_evlu_amt"))
            return BrokerBalance(cash=cash, total_asset=total_asset)
        return BrokerBalance(cash=0.0, total_asset=0.0)

    def get_open_orders(self) -> list[BrokerOrderSnapshot]:
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self.settings.kis_cano,
            "ACNT_PRDT_CD": self.settings.kis_acnt_prdt_cd,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "02",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id="TTTC8001R" if self.settings.env == "real" else "VTTC8001R",
            params=params,
        )
        output = data.get("output1", [])
        snapshots: list[BrokerOrderSnapshot] = []
        if not isinstance(output, list):
            return snapshots
        for item in output:
            remaining_qty = int(item.get("rmn_qty", item.get("nccs_qty", "0")) or 0)
            if remaining_qty <= 0:
                continue
            snapshots.append(
                BrokerOrderSnapshot(
                    order_no=item.get("odno", ""),
                    symbol=item.get("pdno", ""),
                    status=item.get("ord_stts", item.get("ord_tmd", "ACKNOWLEDGED")),
                    filled_qty=int(item.get("tot_ccld_qty", item.get("ccld_qty", "0")) or 0),
                    remaining_qty=remaining_qty,
                )
            )
        return snapshots

    def get_positions(self) -> list[BrokerPositionSnapshot]:
        params = {
            "CANO": self.settings.kis_cano,
            "ACNT_PRDT_CD": self.settings.kis_acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id="TTTC8434R" if self.settings.env == "real" else "VTTC8434R",
            params=params,
        )
        output = data.get("output1", [])
        snapshots: list[BrokerPositionSnapshot] = []
        if not isinstance(output, list):
            return snapshots
        for item in output:
            qty = int(item.get("hldg_qty", item.get("hold_qty", "0")) or 0)
            if qty <= 0:
                continue
            snapshots.append(
                BrokerPositionSnapshot(
                    symbol=item.get("pdno", ""),
                    qty=qty,
                    avg_price=self._to_float(item.get("pchs_avg_pric", item.get("pchs_avg_pric", "0"))),
                    current_price=self._to_float(item.get("prpr", item.get("now_pric", "0"))),
                    name=item.get("prdt_name", item.get("hts_kor_isnm", "")),
                )
            )
        return snapshots

    def get_current_price(self, symbol: str) -> dict[str, float]:
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
            },
        )
        output = data.get("output", {})
        if not isinstance(output, dict):
            return {"price": 0.0, "turnover": 0.0}
        return {
            "price": self._to_float(output.get("stck_prpr")),
            "turnover": self._to_float(output.get("acml_tr_pbmn")),
        }

    def get_daily_bars(self, symbol: str, lookback_days: int = 30) -> list[dict[str, float]]:
        today = datetime.now().strftime("%Y%m%d")
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id="FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_DATE_2": today,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        output = data.get("output2", [])
        bars: list[dict[str, float]] = []
        if not isinstance(output, list):
            return bars
        for item in output[:lookback_days]:
            bars.append(
                {
                    "open": self._to_float(item.get("stck_oprc")),
                    "high": self._to_float(item.get("stck_hgpr")),
                    "low": self._to_float(item.get("stck_lwpr")),
                    "close": self._to_float(item.get("stck_clpr")),
                    "volume": self._to_float(item.get("acml_vol")),
                    "turnover": self._to_float(item.get("acml_tr_pbmn")),
                }
            )
        return bars

    def get_daily_turnover_history(self, symbol: str, lookback_days: int = 30) -> list[dict[str, float]]:
        today = datetime.now().strftime("%Y%m%d")
        bars = self.get_daily_bars(symbol, lookback_days=lookback_days)
        history: list[dict[str, float]] = []
        for item in bars:
            history.append(
                {
                    "close": float(item.get("close") or 0.0),
                    "turnover": float(item.get("turnover") or 0.0),
                }
            )
        return history

    def get_daily_fills(self) -> list[BrokerFillSnapshot]:
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self.settings.kis_cano,
            "ACNT_PRDT_CD": self.settings.kis_acnt_prdt_cd,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "01",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id="TTTC8001R" if self.settings.env == "real" else "VTTC8001R",
            params=params,
        )
        output = data.get("output1", [])
        fills: list[BrokerFillSnapshot] = []
        if not isinstance(output, list):
            return fills
        for item in output:
            fill_qty = int(item.get("tot_ccld_qty", item.get("ccld_qty", "0")) or 0)
            if fill_qty <= 0:
                continue
            fills.append(
                BrokerFillSnapshot(
                    order_no=item.get("odno", ""),
                    symbol=item.get("pdno", ""),
                    side=self._resolve_side(item),
                    fill_qty=fill_qty,
                    fill_price=self._to_float(item.get("avg_prvs", item.get("avg_unpr"))),
                    filled_at=item.get("ord_tmd", ""),
                )
            )
        return fills

    def get_approval_key(self) -> str:
        data = self._request_json(
            method="POST",
            path="/oauth2/Approval",
            body={
                "grant_type": "client_credentials",
                "appkey": self.settings.kis_app_key,
                "secretkey": self.settings.kis_app_secret,
            },
            use_hashkey=False,
            use_authorization=False,
        )
        approval_key = data.get("approval_key", "")
        if isinstance(approval_key, str):
            return approval_key
        return ""

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        tr_id: str | None = None,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        use_hashkey: bool = False,
        use_authorization: bool = True,
        allow_auth_retry: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.settings.kis_base_url.rstrip('/')}{path}"
        if params:
            url = f"{url}?{parse.urlencode(params)}"
        payload = None
        headers = self._build_headers(
            tr_id=tr_id,
            body=body,
            use_hashkey=use_hashkey,
            use_authorization=use_authorization,
        )
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        req = request.Request(url=url, data=payload, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                response_text = response.read().decode("utf-8")
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError as exc:
                    raise BrokerResponseError(f"Invalid JSON response from broker API: {path}") from exc
                if self._should_retry_for_expired_token(data, use_authorization=use_authorization, allow_auth_retry=allow_auth_retry):
                    self._access_token = self._renew_access_token()
                    return self._request_json(
                        method=method,
                        path=path,
                        tr_id=tr_id,
                        params=params,
                        body=body,
                        use_hashkey=use_hashkey,
                        use_authorization=use_authorization,
                        allow_auth_retry=False,
                    )
                return data
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(error_body)
            except json.JSONDecodeError:
                raise BrokerApiError(f"Broker API HTTP error {exc.code}: {error_body}") from exc
            if self._should_retry_for_expired_token(data, use_authorization=use_authorization, allow_auth_retry=allow_auth_retry):
                self._access_token = self._renew_access_token()
                return self._request_json(
                    method=method,
                    path=path,
                    tr_id=tr_id,
                    params=params,
                    body=body,
                    use_hashkey=use_hashkey,
                    use_authorization=use_authorization,
                    allow_auth_retry=False,
                )
            return data
        except error.URLError as exc:
            raise BrokerApiError(f"Broker API connection error for {path}: {exc.reason}") from exc

    def _build_headers(
        self,
        *,
        tr_id: str | None,
        body: dict[str, Any] | None,
        use_hashkey: bool,
        use_authorization: bool,
    ) -> dict[str, str]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "appkey": self.settings.kis_app_key,
            "appsecret": self.settings.kis_app_secret,
            "custtype": "P",
        }
        if use_authorization:
            access_token = self._ensure_access_token()
            if access_token:
                headers["authorization"] = f"Bearer {access_token}"
        if tr_id:
            headers["tr_id"] = tr_id
        if self.settings.kis_user_id:
            headers["personalseckey"] = self.settings.kis_user_id
        if use_hashkey and body is not None:
            headers["hashkey"] = self._get_hashkey(body)
        return headers

    def _ensure_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if self.settings.kis_access_token:
            self._access_token = self.settings.kis_access_token
            return self._access_token
        if self.settings.kis_refresh_token:
            self._access_token = self._refresh_access_token()
            return self._access_token
        self._access_token = self._issue_access_token()
        return self._access_token


    def _renew_access_token(self) -> str:
        if self.settings.kis_refresh_token:
            return self._refresh_access_token()
        return self._issue_access_token()

    def _issue_access_token(self) -> str:
        data = self._request_json(
            method="POST",
            path="/oauth2/tokenP",
            body={
                "grant_type": "client_credentials",
                "appkey": self.settings.kis_app_key,
                "appsecret": self.settings.kis_app_secret,
            },
            use_hashkey=False,
            use_authorization=False,
        )
        token = data.get("access_token", "")
        if isinstance(token, str):
            return token
        return ""

    def _refresh_access_token(self) -> str:
        data = self._request_json(
            method="POST",
            path="/oauth2/tokenP",
            body={
                "grant_type": "refresh_token",
                "appkey": self.settings.kis_app_key,
                "appsecret": self.settings.kis_app_secret,
                "refresh_token": self.settings.kis_refresh_token,
            },
            use_hashkey=False,
            use_authorization=False,
        )
        token = data.get("access_token", "")
        if isinstance(token, str):
            return token
        return ""

    @staticmethod
    def _is_expired_token_message(message: str) -> bool:
        normalized = str(message or "").strip().lower()
        if not normalized:
            return False
        return (
            "expired token" in normalized
            or "token expired" in normalized
            or ("token" in normalized and "expired" in normalized)
            or "기간이 만료된 token" in normalized
            or ("token" in normalized and "만료" in normalized)
        )

    def _should_retry_for_expired_token(
        self,
        data: dict[str, Any],
        *,
        use_authorization: bool,
        allow_auth_retry: bool,
    ) -> bool:
        if not use_authorization or not allow_auth_retry or not isinstance(data, dict):
            return False
        message = str(data.get("msg1", "") or data.get("message", "") or "")
        return self._is_expired_token_message(message)

    def _get_hashkey(self, body: dict[str, Any]) -> str:
        data = self._request_json(
            method="POST",
            path="/uapi/hashkey",
            body=body,
            use_hashkey=False,
        )
        hashkey = data.get("HASH")
        if isinstance(hashkey, str):
            return hashkey
        return ""

    @staticmethod
    def _resolve_order_division(order_type: str) -> str:
        return "01" if order_type == "MARKET" else "00"

    @staticmethod
    def _format_price(price: float | None) -> str:
        if price is None:
            return "0"
        return str(int(price))

    @staticmethod
    def _extract_order_no(data: dict[str, Any]) -> str | None:
        output = data.get("output")
        if isinstance(output, dict):
            for key in ("ODNO", "odno", "ORD_NO", "ord_no"):
                if key in output and output[key]:
                    return str(output[key])
        return None

    @staticmethod
    def _resolve_side(item: dict[str, Any]) -> str:
        side = str(item.get("sll_buy_dvsn_cd", ""))
        return "SELL" if side == "01" else "BUY"

    @staticmethod
    def _to_float(value: Any) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(str(value).replace(",", ""))
        except ValueError:
            return 0.0
