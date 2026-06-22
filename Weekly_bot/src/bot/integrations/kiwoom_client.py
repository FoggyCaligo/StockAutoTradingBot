from __future__ import annotations

import json
import os
import re
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from bot.integrations.kiwoom_models import Fill, HogaLevel, HogaSnapshot, OrderResult, Position
from bot.utils import RateLimiter


class KiwoomClient:
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
        self.default_stex_tp = os.environ.get("KIWOOM_STEX_TP", "1")
        self.default_qry_tp = os.environ.get("KIWOOM_QRY_TP", "1")

        self.access_token: str | None = None
        self.session = requests.Session()
        self._limiter = RateLimiter(int(os.getenv("KIWOOM_RATE_LIMIT_PER_SECOND", "5")))
        self._last_cash_basis_log_signature = ""

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
        raw = self._request(
            "POST",
            self.DOMESTIC_MARKET_COND_PATH,
            api_id=self.TR_KA10004_HOGA,
            json={"stk_cd": ticker},
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

    def buy_market(self, ticker: str, quantity: int) -> OrderResult:
        raw = self._request(
            "POST",
            self.DOMESTIC_ORDER_PATH,
            api_id=self.TR_KT10000_BUY,
            json={
                "dmst_stex_tp": self.default_dmst_stex_tp,
                "stk_cd": ticker,
                "ord_qty": str(quantity),
                "ord_uv": "",
                "trde_tp": "3",
                "cond_uv": "",
            },
        )
        return self._parse_order_result(raw, ticker=ticker, side="BUY", quantity=quantity, price=0)

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        raw = self._request(
            "POST",
            self.DOMESTIC_ORDER_PATH,
            api_id=self.TR_KT10001_SELL,
            json={
                "dmst_stex_tp": self.default_dmst_stex_tp,
                "stk_cd": ticker,
                "ord_qty": str(quantity),
                "ord_uv": "",
                "trde_tp": "3",
                "cond_uv": "",
            },
        )
        return self._parse_order_result(raw, ticker=ticker, side="SELL", quantity=quantity, price=0)

    def get_positions(self) -> list[Position]:
        raw = self._request(
            "POST",
            self.DOMESTIC_ACCOUNT_PATH,
            api_id=self.TR_KT00018_POSITIONS,
            json={"qry_tp": self.default_qry_tp, "dmst_stex_tp": self.default_dmst_stex_tp},
        )
        return self._parse_positions(raw)

    def get_orderable_cash(self) -> int:
        raw = self._request(
            "POST",
            self.DOMESTIC_ACCOUNT_PATH,
            api_id=self.TR_KT00001_CASH,
            json={"qry_tp": "2"},
        )
        orderable_cash = self._extract_orderable_cash(raw)
        self._log_cash_basis_debug(raw, resolved_value=orderable_cash, basis="orderable_cash")
        return orderable_cash

    def get_deposit_cash(self) -> int:
        raw = self._request(
            "POST",
            self.DOMESTIC_ACCOUNT_PATH,
            api_id=self.TR_KT00001_CASH,
            json={"qry_tp": "2"},
        )
        deposit_cash = self._extract_deposit_cash(raw)
        self._log_cash_basis_debug(raw, resolved_value=deposit_cash, basis="deposit_cash")
        return deposit_cash

    def get_open_orders(self) -> list[dict[str, Any]]:
        raw = self._request(
            "POST",
            self.DOMESTIC_ACCOUNT_PATH,
            api_id=self.TR_KA10075_OPEN_ORDERS,
            json={
                "all_stk_tp": "0",
                "trde_tp": "0",
                "stk_cd": "",
                "stex_tp": self.default_stex_tp,
            },
        )
        orders = self._extract_list(raw, ["oso"])
        return orders if isinstance(orders, list) else []

    def get_order_status(self, order_id: str, ticker: str = "", side: str = "") -> dict[str, Any]:
        rows = self.get_fills(ticker=ticker, sell_tp=self._resolve_sell_tp(side))
        return {"cntr": self._filter_rows_for_order(rows, order_id)}

    def get_fills(self, ticker: str = "", sell_tp: str = "0", limit_pages: int = 10) -> list[dict[str, Any]]:
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
        return self._build_fill_from_rows(self.get_fills(sell_tp="2"), order_id)

    def get_order_fill(self, order_id: str, ticker: str = "", side: str = "") -> Fill | None:
        return self._build_fill_from_rows(
            self.get_fills(ticker=ticker, sell_tp=self._resolve_sell_tp(side)),
            order_id,
        )

    def _parse_order_result(self, raw: dict[str, Any], ticker: str, side: str, quantity: int, price: int) -> OrderResult:
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
        matching_rows = sorted(matching_rows, key=self._fill_sort_key)

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
        return Fill(
            order_id=order_id,
            ticker=ticker,
            quantity=total_quantity,
            price=price,
            filled_at=self._parse_fill_time(latest_row),
            raw={"rows": matching_rows, "latest_row": latest_row},
        )

    def _resolve_sell_tp(self, side: str) -> str:
        normalized = str(side or "").strip().upper()
        if normalized == "SELL":
            return "1"
        if normalized == "BUY":
            return "2"
        return "0"

    def _fill_sort_key(self, row: dict[str, Any]) -> tuple[str, str]:
        return str(row.get("ord_tm") or row.get("tm") or "").strip(), str(row.get("cntr_no") or "").strip()

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
        return [row for row in rows if str(row.get("orig_ord_no") or "").strip() == normalized_order_id]

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
        margin_bucket_keys = [
            "20stk_ord_alow_amt",
            "30stk_ord_alow_amt",
            "40stk_ord_alow_amt",
            "50stk_ord_alow_amt",
            "60stk_ord_alow_amt",
            "100stk_ord_alow_amt",
            "100_stk_ord_alow_amt",
            "stock_100_ord_alow_amt",
        ]
        margin_bucket_value = max((self._extract_number(raw, [key]) for key in margin_bucket_keys), default=0)
        if margin_bucket_value > 0:
            return margin_bucket_value

        preferred_keys = [
            "ord_psbl_cash",
            "ord_psbl_amt",
            "buy_psbl_cash",
            "buy_psbl_amt",
            "mgn100_ord_psbl_amt",
            "mgn_100_ord_psbl_amt",
            "stock_ord_psbl_amt",
            "cash_ord_psbl_amt",
        ]
        preferred_value = self._extract_number(raw, preferred_keys)
        if preferred_value > 0:
            return preferred_value

        generic_orderable = self._extract_number(raw, ["ord_alow_amt", "ord_alowa", "wthd_alowa", "pymn_alow_amt"])
        if generic_orderable > 0:
            return generic_orderable

        return self._extract_number(raw, ["elwdpst_evlta", "dnca_tot_amt", "deposit", "cash"])

    def _candidate_orderable_cash_values(self, raw: Any) -> dict[str, int]:
        candidate_keys = [
            "20stk_ord_alow_amt",
            "30stk_ord_alow_amt",
            "40stk_ord_alow_amt",
            "50stk_ord_alow_amt",
            "60stk_ord_alow_amt",
            "100stk_ord_alow_amt",
            "100_stk_ord_alow_amt",
            "stock_100_ord_alow_amt",
            "ord_psbl_cash",
            "ord_psbl_amt",
            "buy_psbl_cash",
            "buy_psbl_amt",
            "mgn100_ord_psbl_amt",
            "mgn_100_ord_psbl_amt",
            "stock_ord_psbl_amt",
            "cash_ord_psbl_amt",
            "ord_alow_amt",
            "ord_alowa",
            "wthd_alowa",
            "pymn_alow_amt",
            "elwdpst_evlta",
            "dnca_tot_amt",
            "deposit",
            "cash",
        ]
        return {key: self._extract_number(raw, [key]) for key in candidate_keys}

    def _extract_deposit_cash(self, raw: Any) -> int:
        preferred_keys = [
            "dnca_tot_amt",
            "deposit",
            "cash",
            "elwdpst",
            "d2_dps",
            "ord_psbl_cash",
        ]
        return self._extract_number(raw, preferred_keys)

    def _cash_basis_debug_log_path(self) -> Path:
        root = Path(__file__).resolve().parents[4]
        return root / "logs" / f"kt00001_cash_debug_{datetime.now().strftime('%Y%m%d')}.jsonl"

    def _log_cash_basis_debug(self, raw: Any, resolved_value: int, basis: str) -> None:
        try:
            candidate_values = self._candidate_orderable_cash_values(raw)
            signature = json.dumps(
                {
                    "basis": basis,
                    "resolved_value": resolved_value,
                    "nonzero_candidate_values": {key: value for key, value in candidate_values.items() if value > 0},
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if signature == self._last_cash_basis_log_signature:
                return
            self._last_cash_basis_log_signature = signature

            payload = {
                "logged_at": datetime.now().isoformat(),
                "basis": basis,
                "resolved_value": resolved_value,
                "candidate_values": candidate_values,
                "raw": raw,
            }
            log_path = self._cash_basis_debug_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"Failed to log kt00001 cash debug payload: {exc}")

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        normalized = ticker.strip()
        if re.match(r"^[A-Z][0-9]{6}$", normalized):
            return normalized[1:]
        return normalized
