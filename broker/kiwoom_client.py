from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv

from models import Fill, HogaLevel, HogaSnapshot, OrderResult, Position
from utils import RateLimiter


class KiwoomClient:
    TOKEN_PATH = os.getenv("KIWOOM_TOKEN_PATH", "/oauth2/token")
    HOGA_PATH = os.getenv("KIWOOM_HOGA_PATH", "/uapi/domestic-stock/v1/quotations/inquire-price")
    ORDER_PATH = os.getenv("KIWOOM_ORDER_PATH", "/uapi/domestic-stock/v1/trading/order-cash")
    CANCEL_ORDER_PATH = os.getenv("KIWOOM_CANCEL_ORDER_PATH", "/uapi/domestic-stock/v1/trading/order-cancel")
    POSITION_PATH = os.getenv("KIWOOM_POSITION_PATH", "/uapi/domestic-stock/v1/trading/inquire-balance")
    OPEN_ORDERS_PATH = os.getenv("KIWOOM_OPEN_ORDERS_PATH", "/uapi/domestic-stock/v1/trading/inquire-order")
    ORDER_STATUS_PATH = os.getenv("KIWOOM_ORDER_STATUS_PATH", "/uapi/domestic-stock/v1/trading/inquire-order-detail")
    DEFAULT_RATE_LIMIT_PER_SECOND = int(os.getenv("KIWOOM_RATE_LIMIT_PER_SECOND", "5"))

    def __init__(self, base_url: str | None = None):
        load_dotenv()
        self.base_url = base_url or os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com")
        self.app_key = os.environ.get("KIWOOM_APP_KEY", "")
        self.app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
        self.account_no = os.environ.get("KIWOOM_ACCOUNT_NO", "")
        self.access_token: str | None = None
        self.session = requests.Session()
        self._limiter = RateLimiter(self.DEFAULT_RATE_LIMIT_PER_SECOND)

    def auth(self) -> str:
        if not self.app_key or not self.app_secret:
            raise RuntimeError("Kiwoom credentials are not configured. Set KIWOOM_APP_KEY and KIWOOM_APP_SECRET.")

        url = self._build_url(self.TOKEN_PATH)
        payload = {"grant_type": "client_credentials"}
        headers = {
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = self.session.post(url, data=payload, headers=headers, timeout=20)
        response.raise_for_status()
        body = response.json()

        token = self._extract_value(body, ["access_token", "accessToken", "ACCESS_TOKEN"])
        if not token:
            raise RuntimeError(f"Failed to obtain Kiwoom access token: {body}")

        self.access_token = token
        return token

    def _build_url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Call auth() before API requests.")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "Content-Type": "application/json;charset=UTF-8",
        }

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> dict[str, Any]:
        self._limiter.wait()
        url = self._build_url(path)
        response = self.session.request(method, url, headers=self._headers(), params=params, json=json, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_20hoga(self, ticker: str) -> HogaSnapshot:
        params = {
            "fid_cond_mrkt_div_code": "J",  # 국내 주식
            "fid_input_iscd": ticker,
        }
        raw = self._request("GET", self.HOGA_PATH, params=params)

        current_price = self._extract_number(raw, ["stck_prpr", "current_price", "price", "currentPrice"])
        bids = self._extract_hoga_levels(raw, side="bid")
        asks = self._extract_hoga_levels(raw, side="ask")

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
        return self._submit_order(
            ticker=ticker,
            side="BUY",
            quantity=quantity,
            price=price,
            order_type="LIMIT",
        )

    def buy_market(self, ticker: str, quantity: int) -> OrderResult:
        return self._submit_order(
            ticker=ticker,
            side="BUY",
            quantity=quantity,
            price=0,
            order_type="MARKET",
        )

    def sell_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        return self._submit_order(
            ticker=ticker,
            side="SELL",
            quantity=quantity,
            price=price,
            order_type="LIMIT",
        )

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        return self._submit_order(
            ticker=ticker,
            side="SELL",
            quantity=quantity,
            price=0,
            order_type="MARKET",
        )

    def cancel_order(self, order_id: str) -> None:
        if not order_id:
            return
        payload = {
            "CANO": self.account_no,
            "ORD_NO": order_id,
        }
        self._request("POST", self.CANCEL_ORDER_PATH, json=payload)

    def get_positions(self) -> list[Position]:
        raw = self._request("GET", self.POSITION_PATH, params={"CANO": self.account_no})
        return self._parse_positions(raw)

    def get_open_orders(self) -> list[dict[str, Any]]:
        raw = self._request("GET", self.OPEN_ORDERS_PATH, params={"CANO": self.account_no})
        orders = self._extract_list(raw, ["output", "output1", "orders", "open_orders"])
        return orders if isinstance(orders, list) else []

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        raw = self._request("GET", self.ORDER_STATUS_PATH, params={"CANO": self.account_no, "ORD_NO": order_id})
        return raw

    def wait_buy_filled(self, order_id: str, timeout_seconds: int = 30) -> Fill | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            raw = self.get_order_status(order_id)
            status = self._extract_value(raw, ["ord_stat", "order_status", "status"])
            if self._is_filled_status(status):
                quantity = self._extract_number(raw, ["ord_qty", "quantity", "filled_qty", "fill_quantity"])
                price = self._extract_number(raw, ["ord_unpr", "price", "filled_price", "fill_price"])
                return Fill(order_id=order_id, ticker="", quantity=quantity, price=price, raw=raw)
            time.sleep(2)
        return None

    def wait_until_no_position(self, timeout_seconds: int = 60) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.get_positions():
                return True
            time.sleep(2)
        return False

    def _submit_order(self, ticker: str, side: str, quantity: int, price: int, order_type: str) -> OrderResult:
        payload = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01",
            "PDNO": ticker,
            "ORD_DVSN_CODE": "01" if side == "BUY" else "02",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
            "ORD_TP": order_type,
        }
        raw = self._request("POST", self.ORDER_PATH, json=payload)
        return self._parse_order_result(raw)

    def _parse_order_result(self, raw: dict[str, Any]) -> OrderResult:
        return OrderResult(
            order_id=self._extract_value(raw, ["ord_no", "order_id", "orderId", "id"]),
            ticker=self._extract_value(raw, ["pdno", "ticker", "symbol"]),
            side=self._extract_value(raw, ["ord_dvsn_code", "side", "order_side"]),
            quantity=self._extract_number(raw, ["ord_qty", "quantity", "qty"]),
            price=self._extract_number(raw, ["ord_unpr", "price", "order_price"]),
            status=self._extract_value(raw, ["ord_stat", "status", "order_status"]),
            raw=raw,
        )

    def _parse_positions(self, raw: dict[str, Any]) -> list[Position]:
        position_list = self._extract_list(raw, ["output", "output1", "positions", "balance"])
        if not isinstance(position_list, list):
            return []
        positions: list[Position] = []
        for item in position_list:
            ticker = self._extract_value(item, ["pdno", "ticker", "symbol"])
            quantity = self._extract_number(item, ["hldg_qty", "quantity", "hold_qty", "qty"])
            avg_price = self._extract_number(item, ["avg_pric", "avg_price", "avgPrc", "avg_price"])
            if ticker and quantity > 0:
                positions.append(Position(ticker=ticker, quantity=quantity, avg_price=avg_price, raw=item))
        return positions

    def _extract_hoga_levels(self, raw: dict[str, Any], side: str) -> list[HogaLevel]:
        side_keys = {
            "bid": ["bid", "bids", "buy", "buy_side", "bid_price"],
            "ask": ["ask", "asks", "sell", "sell_side", "ask_price"],
        }
        candidates = ["output1", "output2", "output3", "orderbook", "hoga", "data", "orderbook_data"]

        for group in candidates:
            segment = self._find_value(raw, group)
            if isinstance(segment, list) and segment:
                levels = self._build_levels(segment, side)
                if levels:
                    return levels

        return []

    def _build_levels(self, rows: list[Any], side: str) -> list[HogaLevel]:
        levels: list[HogaLevel] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = self._extract_number(row, ["ord_unpr", "price", "hoga_price", "bid_price", "ask_price", "price"])
            volume = self._extract_number(row, ["ord_qty", "qty", "volume", "vol", "rem_qty", "rem_volume"])
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

    def _find_value(self, raw: Any, key: str) -> Any:
        if isinstance(raw, dict):
            if key in raw:
                return raw[key]
            for value in raw.values():
                result = self._find_value(value, key)
                if result is not None:
                    return result
        return None

    def _extract_number(self, raw: Any, keys: list[str]) -> int:
        text = self._extract_value(raw, keys)
        if text is None:
            return 0
        if isinstance(text, (int, float)):
            return int(text)
        try:
            return int(str(text).replace(",", "").strip())
        except ValueError:
            try:
                return int(float(str(text).replace(",", "").strip()))
            except ValueError:
                return 0

    def _is_filled_status(self, status: str | None) -> bool:
        if not status:
            return False
        normalized = status.strip().upper()
        return normalized in {"F", "FILLED", "COMPLETED", "체결", "완료"}
