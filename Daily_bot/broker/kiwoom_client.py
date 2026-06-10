from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv

from Daily_bot.models import Fill, HogaLevel, HogaSnapshot, OrderResult, Position
from Daily_bot.utils import RateLimiter


class KiwoomClient:
    """Kiwoom REST client aligned to official endpoint/field names."""

    TOKEN_PATH = "/oauth2/token"
    DOMESTIC_ORDER_PATH = "/api/dostk/ordr"
    DOMESTIC_ACCOUNT_PATH = "/api/dostk/acnt"
    DOMESTIC_MARKET_COND_PATH = "/api/dostk/mrkcond"

    TR_KA10004_HOGA = "ka10004"
    TR_KT10000_BUY = "kt10000"
    TR_KT10001_SELL = "kt10001"
    TR_KT10003_CANCEL = "kt10003"
    TR_KT00001_CASH = "kt00001"
    TR_KT00018_POSITIONS = "kt00018"
    TR_KA10075_OPEN_ORDERS = "ka10075"
    TR_KA10076_FILLS = "ka10076"

    def __init__(self, base_url: str | None = None):
        load_dotenv()
        self.base_url = base_url or os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com")
        self.app_key = os.environ.get("KIWOOM_APP_KEY", "")
        self.app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
        self.account_no = os.environ.get("KIWOOM_ACCOUNT_NO", "")
        self.default_dmst_stex_tp = os.environ.get("KIWOOM_DMST_STEX_TP", "KRX")
        self.default_stex_tp = os.environ.get("KIWOOM_STEX_TP", "1")  # 0:통합, 1:KRX, 2:NXT
        self.default_qry_tp = os.environ.get("KIWOOM_QRY_TP", "1")  # 1:합산, 2:개별

        self.access_token: str | None = None
        self.session = requests.Session()
        self._limiter = RateLimiter(int(os.getenv("KIWOOM_RATE_LIMIT_PER_SECOND", "5")))

    def auth(self) -> str:
        if not self.app_key or not self.app_secret:
            raise RuntimeError("Kiwoom credentials are not configured. Set KIWOOM_APP_KEY and KIWOOM_APP_SECRET.")

        url = self._build_url(self.TOKEN_PATH)
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        headers = {"Content-Type": "application/json;charset=UTF-8"}

        response = self.session.post(url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        body = response.json()

        token = self._extract_value(body, ["token", "access_token", "accessToken", "ACCESS_TOKEN"])
        if not token:
            raise RuntimeError(f"Failed to obtain Kiwoom access token: {body}")

        self.access_token = token
        return token

    def _build_url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url.rstrip('/')}{normalized}"

    def _headers(self, api_id: str, cont_yn: str = "N", next_key: str = "") -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Call auth() before API requests.")
        return {
            "authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }

    def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        cont_yn: str = "N",
        next_key: str = "",
    ) -> dict[str, Any]:
        self._limiter.wait()
        url = self._build_url(path)
        response = self.session.request(
            method,
            url,
            headers=self._headers(api_id=api_id, cont_yn=cont_yn, next_key=next_key),
            params=params,
            json=json,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _request_response(
        self,
        method: str,
        path: str,
        api_id: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        cont_yn: str = "N",
        next_key: str = "",
    ) -> requests.Response:
        self._limiter.wait()
        url = self._build_url(path)
        response = self.session.request(
            method,
            url,
            headers=self._headers(api_id=api_id, cont_yn=cont_yn, next_key=next_key),
            params=params,
            json=json,
            timeout=30,
        )
        response.raise_for_status()
        return response

    def get_20hoga(self, ticker: str) -> HogaSnapshot:
        payload = {"stk_cd": ticker}
        raw = self._request(
            "POST",
            self.DOMESTIC_MARKET_COND_PATH,
            api_id=self.TR_KA10004_HOGA,
            json=payload,
        )

        bids = self._extract_hoga_levels(raw, side="bid")
        asks = self._extract_hoga_levels(raw, side="ask")
        current_price = self._extract_number(raw, ["cur_prc", "close_pric", "pred_close_pric", "sel_fpr_bid", "buy_fpr_bid"])
        if current_price <= 0 and bids and asks:
            current_price = (bids[0].price + asks[0].price) // 2

        if not bids or not asks:
            raise RuntimeError(f"Unable to parse 20hoga snapshot for {ticker}. Raw response: {raw}")

        return HogaSnapshot(
            ticker=ticker,
            current_price=current_price,
            bids=bids,
            asks=asks,
            captured_at=datetime.now(),
            raw=raw,
        )

    def buy_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        payload = {
            "dmst_stex_tp": self.default_dmst_stex_tp,
            "stk_cd": ticker,
            "ord_qty": str(quantity),
            "ord_uv": str(price),
            "trde_tp": "0",
            "cond_uv": "",
        }
        raw = self._request("POST", self.DOMESTIC_ORDER_PATH, api_id=self.TR_KT10000_BUY, json=payload)
        return self._parse_order_result(raw, ticker=ticker, side="BUY", quantity=quantity, price=price)

    def buy_market(self, ticker: str, quantity: int) -> OrderResult:
        payload = {
            "dmst_stex_tp": self.default_dmst_stex_tp,
            "stk_cd": ticker,
            "ord_qty": str(quantity),
            "ord_uv": "",
            "trde_tp": "3",
            "cond_uv": "",
        }
        raw = self._request("POST", self.DOMESTIC_ORDER_PATH, api_id=self.TR_KT10000_BUY, json=payload)
        return self._parse_order_result(raw, ticker=ticker, side="BUY", quantity=quantity, price=0)

    def sell_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        payload = {
            "dmst_stex_tp": self.default_dmst_stex_tp,
            "stk_cd": ticker,
            "ord_qty": str(quantity),
            "ord_uv": str(price),
            "trde_tp": "0",
            "cond_uv": "",
        }
        raw = self._request("POST", self.DOMESTIC_ORDER_PATH, api_id=self.TR_KT10001_SELL, json=payload)
        return self._parse_order_result(raw, ticker=ticker, side="SELL", quantity=quantity, price=price)

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        payload = {
            "dmst_stex_tp": self.default_dmst_stex_tp,
            "stk_cd": ticker,
            "ord_qty": str(quantity),
            "ord_uv": "",
            "trde_tp": "3",
            "cond_uv": "",
        }
        raw = self._request("POST", self.DOMESTIC_ORDER_PATH, api_id=self.TR_KT10001_SELL, json=payload)
        return self._parse_order_result(raw, ticker=ticker, side="SELL", quantity=quantity, price=0)

    def cancel_order(self, order_id: str, ticker: str = "", quantity: int = 0) -> None:
        if not order_id:
            return
        payload = {
            "dmst_stex_tp": self.default_dmst_stex_tp,
            "orig_ord_no": order_id,
            "stk_cd": ticker,
            "cncl_qty": str(quantity),
        }
        self._request("POST", self.DOMESTIC_ORDER_PATH, api_id=self.TR_KT10003_CANCEL, json=payload)

    def get_positions(self) -> list[Position]:
        payload = {
            "qry_tp": self.default_qry_tp,
            "dmst_stex_tp": self.default_dmst_stex_tp,
        }
        raw = self._request("POST", self.DOMESTIC_ACCOUNT_PATH, api_id=self.TR_KT00018_POSITIONS, json=payload)
        return self._parse_positions(raw)

    def get_orderable_cash(self) -> int:
        """Return conservative stock-buying power (KRW) from account endpoint."""
        payload = {
            "qry_tp": "2",  # 일반조회
        }
        raw = self._request("POST", self.DOMESTIC_ACCOUNT_PATH, api_id=self.TR_KT00001_CASH, json=payload)
        return self._extract_orderable_cash(raw)

    def get_open_orders(self) -> list[dict[str, Any]]:
        payload = {
            "all_stk_tp": "0",
            "trde_tp": "0",
            "stk_cd": "",
            "stex_tp": self.default_stex_tp,
        }
        raw = self._request("POST", self.DOMESTIC_ACCOUNT_PATH, api_id=self.TR_KA10075_OPEN_ORDERS, json=payload)
        orders = self._extract_list(raw, ["oso"])
        return orders if isinstance(orders, list) else []

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        rows = self.get_fills()
        matching_rows = self._filter_rows_for_order(rows, order_id)
        return {"cntr": matching_rows}

    def get_fills(
        self,
        ticker: str = "",
        sell_tp: str = "0",
        limit_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Fetch today's fills from ka10076.

        Note: ka10076's `ord_no` is a cursor for older fills, not an exact order-id filter.
        We therefore query the recent fill list and search within the returned rows.
        """
        payload = {
            "stk_cd": self._normalize_ticker(ticker),
            "qry_tp": "1" if ticker else "0",
            "sell_tp": sell_tp,
            "ord_no": "",
            "stex_tp": self.default_stex_tp,
        }
        rows: list[dict[str, Any]] = []
        cont_yn = "N"
        next_key = ""

        for _ in range(max(1, limit_pages)):
            response = self._request_response(
                "POST",
                self.DOMESTIC_ACCOUNT_PATH,
                api_id=self.TR_KA10076_FILLS,
                json=payload,
                cont_yn=cont_yn,
                next_key=next_key,
            )
            body = response.json()
            page_rows = self._extract_list(body, ["cntr"]) or []
            if isinstance(page_rows, list):
                rows.extend(page_rows)

            cont_yn = str(response.headers.get("cont-yn") or "N").strip().upper()
            next_key = str(response.headers.get("next-key") or "").strip()
            if cont_yn != "Y" or not next_key:
                break

        return rows

    def get_buy_fill(self, order_id: str) -> Fill | None:
        rows = self.get_fills(sell_tp="2")
        return self._build_fill_from_rows(rows, order_id)

    def get_order_fill(self, order_id: str) -> Fill | None:
        rows = self.get_fills()
        return self._build_fill_from_rows(rows, order_id)

    def wait_buy_filled(
        self,
        order_id: str,
        expected_quantity: int | None = None,
        timeout_seconds: int = 30,
    ) -> Fill | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            fill = self.get_buy_fill(order_id)
            if fill is not None and (expected_quantity is None or fill.quantity >= expected_quantity):
                return fill
            time.sleep(2)
        return None

    def wait_until_no_position(self, timeout_seconds: int = 60) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.get_positions():
                return True
            time.sleep(2)
        return False

    def _parse_order_result(
        self,
        raw: dict[str, Any],
        ticker: str,
        side: str,
        quantity: int,
        price: int,
    ) -> OrderResult:
        return OrderResult(
            order_id=self._extract_value(raw, ["ord_no"]) or "",
            ticker=ticker,
            side=side,
            quantity=quantity,
            price=price,
            status=self._extract_value(raw, ["return_msg"]) or "SUBMITTED",
            raw=raw,
        )

    def _parse_positions(self, raw: dict[str, Any]) -> list[Position]:
        position_list = self._extract_list(raw, ["acnt_evlt_remn_indv_tot"])
        if not isinstance(position_list, list):
            return []
        positions: list[Position] = []
        for item in position_list:
            ticker = self._normalize_ticker(self._extract_value(item, ["stk_cd"]) or "")
            quantity = self._extract_number(item, ["rmnd_qty"])
            avg_price = self._extract_number(item, ["pur_pric"])
            if ticker and quantity > 0:
                positions.append(Position(ticker=ticker, quantity=quantity, avg_price=avg_price, raw=item))
        return positions

    def _build_fill_from_rows(self, rows: list[dict[str, Any]], order_id: str) -> Fill | None:
        matching_rows = self._filter_rows_for_order(rows, order_id)
        if not matching_rows:
            return None

        total_quantity = sum(self._extract_number(row, ["cntr_qty"]) for row in matching_rows)
        latest_row = matching_rows[-1]
        total_amount = sum(
            self._extract_number(row, ["cntr_pric", "ord_pric"]) * self._extract_number(row, ["cntr_qty"])
            for row in matching_rows
        )
        price = int(round(total_amount / total_quantity)) if total_quantity > 0 and total_amount > 0 else 0
        ticker = self._normalize_ticker(self._extract_value(latest_row, ["stk_cd"]) or "")
        if total_quantity <= 0 or price <= 0:
            return None
        filled_at = self._parse_fill_time(latest_row)

        return Fill(
            order_id=order_id,
            ticker=ticker,
            quantity=total_quantity,
            price=price,
            filled_at=filled_at,
            raw={"rows": matching_rows, "latest_row": latest_row},
        )

    def _parse_fill_time(self, row: dict[str, Any]) -> datetime:
        raw_time = str(row.get("ord_tm") or row.get("tm") or "").strip()
        if re.fullmatch(r"\d{6}", raw_time):
            now = datetime.now()
            try:
                return datetime(
                    year=now.year,
                    month=now.month,
                    day=now.day,
                    hour=int(raw_time[0:2]),
                    minute=int(raw_time[2:4]),
                    second=int(raw_time[4:6]),
                )
            except ValueError:
                pass
        return datetime.now()

    def _filter_rows_for_order(self, rows: list[dict[str, Any]], order_id: str) -> list[dict[str, Any]]:
        normalized_order_id = str(order_id).strip()
        if not normalized_order_id:
            return rows

        direct_matches = [row for row in rows if str(row.get("ord_no") or "").strip() == normalized_order_id]
        if direct_matches:
            return direct_matches

        origin_matches = [row for row in rows if str(row.get("orig_ord_no") or "").strip() == normalized_order_id]
        if origin_matches:
            return origin_matches

        return []

    def _extract_hoga_levels(self, raw: dict[str, Any], side: str) -> list[HogaLevel]:
        levels: list[HogaLevel] = []

        if side == "ask":
            first_price_key = "sel_fpr_bid"
            first_volume_key = "sel_fpr_req"
            prefix = "sel"
        else:
            first_price_key = "buy_fpr_bid"
            first_volume_key = "buy_fpr_req"
            prefix = "buy"

        first_price = self._extract_number(raw, [first_price_key])
        first_volume = self._extract_number(raw, [first_volume_key])
        if first_price > 0 and first_volume >= 0:
            levels.append(HogaLevel(price=first_price, volume=first_volume))

        for level in range(2, 11):
            price_key = f"{prefix}_{level}th_pre_bid"
            volume_key = f"{prefix}_{level}th_pre_req"
            price = self._extract_number(raw, [price_key])
            volume = self._extract_number(raw, [volume_key])
            if price > 0 and volume >= 0:
                levels.append(HogaLevel(price=price, volume=volume))

        if side == "bid":
            levels.sort(key=lambda x: x.price, reverse=True)
        else:
            levels.sort(key=lambda x: x.price)
        return levels

    def _extract_value(self, raw: Any, keys: list[str]) -> str | None:
        if isinstance(raw, dict):
            for key in keys:
                if key in raw and raw[key] is not None:
                    return str(raw[key])
            for value in raw.values():
                result = self._extract_value(value, keys)
                if result is not None:
                    return result
        elif isinstance(raw, list):
            for item in raw:
                result = self._extract_value(item, keys)
                if result is not None:
                    return result
        return None

    def _extract_list(self, raw: Any, keys: list[str]) -> list[Any] | None:
        if isinstance(raw, dict):
            for key in keys:
                if key in raw and isinstance(raw[key], list):
                    return raw[key]
            for value in raw.values():
                result = self._extract_list(value, keys)
                if result is not None:
                    return result
        return None

    def _extract_number(self, raw: Any, keys: list[str]) -> int:
        text = self._extract_value(raw, keys)
        if text is None:
            return 0
        cleaned = str(text).replace(",", "").strip().replace("+", "")
        try:
            return abs(int(cleaned))
        except ValueError:
            try:
                return abs(int(float(cleaned)))
            except ValueError:
                return 0

    def _extract_orderable_cash(self, raw: Any) -> int:
        # Kiwoom's generic ord_alow_amt can be much lower than stock-buying power.
        # Prefer the conservative "100% margin stock orderable amount", then fall back.
        preferred_keys = [
            "100stk_ord_alow_amt",
            "100_stk_ord_alow_amt",
            "stock_100_ord_alow_amt",
            "elwdpst_evlta",
        ]
        preferred_value = self._extract_number(raw, preferred_keys)
        if preferred_value > 0:
            return preferred_value

        return self._extract_number(
            raw,
            [
                "ord_alow_amt",
                "ord_alowa",
                "wthd_alowa",
                "pymn_alow_amt",
            ],
        )

    def _normalize_ticker(self, ticker: str) -> str:
        normalized = ticker.strip()
        if re.match(r"^[A-Z][0-9]{6}$", normalized):
            return normalized[1:]
        return normalized
