import Daily_bot.broker.kiwoom_client as kiwoom_module
from Daily_bot.broker.kiwoom_client import KiwoomClient
import requests


def test_get_orderable_cash_prefers_stock_buying_power(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = {
        "elwdpst_evlta": "000000000211315",
        "pymn_alow_amt": "000000000024449",
        "ord_alow_amt": "000000000024449",
        "40stk_ord_alow_amt": "000000001899472",
        "100stk_ord_alow_amt": "000000000211315",
        "return_code": 0,
    }
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    result = client.get_orderable_cash()

    assert result == 1_899_472


def test_get_orderable_cash_prefers_explicit_orderable_amount_over_deposit_like_value(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = {
        "elwdpst_evlta": "000000005000000",
        "ord_psbl_amt": "000000001250000",
        "ord_alow_amt": "000000000900000",
        "return_code": 0,
    }
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    result = client.get_orderable_cash()

    assert result == 1_250_000


def test_get_orderable_cash_prefers_highest_margin_bucket_when_present(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = {
        "20stk_ord_alow_amt": "000000001500000",
        "40stk_ord_alow_amt": "000000002100000",
        "60stk_ord_alow_amt": "000000001300000",
        "100stk_ord_alow_amt": "000000000800000",
        "ord_alow_amt": "000000000600000",
        "elwdpst_evlta": "000000000820000",
        "return_code": 0,
    }
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    result = client.get_orderable_cash()

    assert result == 2_100_000


def test_get_orderable_cash_falls_back_to_generic_amount_when_stock_buying_power_missing(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = {
        "pymn_alow_amt": "000000000024449",
        "ord_alow_amt": "000000000024449",
        "return_code": 0,
    }
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    result = client.get_orderable_cash()

    assert result == 24449


def test_get_orderable_cash_uses_deposit_like_value_only_as_last_resort(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = {
        "elwdpst_evlta": "000000000211315",
        "return_code": 0,
    }
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    result = client.get_orderable_cash()

    assert result == 211315


def test_get_orderable_cash_writes_debug_log_once_per_unique_signature(monkeypatch, tmp_path):
    client = KiwoomClient(base_url="https://example.com")
    response = {
        "40stk_ord_alow_amt": "000000001899472",
        "ord_psbl_amt": "000000001250000",
        "elwdpst_evlta": "000000005000000",
        "return_code": 0,
    }
    log_path = tmp_path / "kt00001_cash_debug.jsonl"
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)
    monkeypatch.setattr(client, "_orderable_cash_debug_log_path", lambda: log_path)

    first = client.get_orderable_cash()
    second = client.get_orderable_cash()

    assert first == 1_899_472
    assert second == 1_899_472
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"resolved_orderable_cash": 1899472' in lines[0]
    assert '"40stk_ord_alow_amt": 1899472' in lines[0]
    assert '"ord_psbl_amt": 1250000' in lines[0]


def test_wait_buy_filled_requires_full_quantity(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    responses = [
        [
            {"ord_no": "BUY-1", "stk_cd": "A005930", "cntr_qty": "3", "cntr_pric": "10000", "ord_tm": "090001"},
        ],
        [
            {"ord_no": "BUY-1", "stk_cd": "A005930", "cntr_qty": "3", "cntr_pric": "10000", "ord_tm": "090001"},
            {"ord_no": "BUY-1", "stk_cd": "A005930", "cntr_qty": "7", "cntr_pric": "10050", "ord_tm": "090002"},
        ],
    ]
    monotonic_values = iter([0.0, 0.1, 0.2, 0.3])

    monkeypatch.setattr(client, "get_fills", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(kiwoom_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(kiwoom_module.time, "monotonic", lambda: next(monotonic_values))

    fill = client.wait_buy_filled("BUY-1", expected_quantity=10, timeout_seconds=1)

    assert fill is not None
    assert fill.ticker == "005930"
    assert fill.quantity == 10
    assert fill.price == 10035


def test_get_buy_fill_filters_out_other_orders_in_same_response(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = [
        {"ord_no": "0145236", "stk_cd": "003000", "cntr_qty": "5", "cntr_pric": "5020", "ord_tm": "090001"},
        {"ord_no": "0214741", "stk_cd": "A012030", "cntr_qty": "26", "cntr_pric": "2200", "ord_tm": "090002"},
        {"ord_no": "0145506", "stk_cd": "003280", "cntr_qty": "10", "cntr_pric": "2295", "ord_tm": "090003"},
    ]
    monkeypatch.setattr(client, "get_fills", lambda *args, **kwargs: response)

    fill = client.get_buy_fill("0214741")

    assert fill is not None
    assert fill.ticker == "012030"
    assert fill.quantity == 26
    assert fill.price == 2200


def test_get_buy_fill_returns_none_when_order_not_present_in_response(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = [
        {"ord_no": "0145236", "stk_cd": "003000", "cntr_qty": "5", "cntr_pric": "5020", "ord_tm": "090001"},
    ]
    monkeypatch.setattr(client, "get_fills", lambda *args, **kwargs: response)

    fill = client.get_buy_fill("0214741")

    assert fill is None


def test_get_order_fill_uses_weighted_average_execution_price(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = [
        {"ord_no": "SELL-1", "stk_cd": "A005930", "cntr_qty": "3", "cntr_pric": "10000", "ord_tm": "090001"},
        {"ord_no": "SELL-1", "stk_cd": "A005930", "cntr_qty": "7", "cntr_pric": "10050", "ord_tm": "090002"},
    ]
    monkeypatch.setattr(client, "get_fills", lambda *args, **kwargs: response)

    fill = client.get_order_fill("SELL-1")

    assert fill is not None
    assert fill.quantity == 10
    assert fill.price == 10035


class _ResponseStub:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.headers = {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_request_retries_after_transient_connection_error(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    client.access_token = "token"
    responses = iter(
        [
            requests.exceptions.ConnectionError("temporary disconnect"),
            _ResponseStub(200, {"return_code": 0}),
        ]
    )

    def _request_once(*_args, **_kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client.session, "request", _request_once)
    monkeypatch.setattr(kiwoom_module.time, "sleep", lambda *_args, **_kwargs: None)

    body = client._request("POST", "/api/test", api_id="test", json={})

    assert body == {"return_code": 0}


def test_request_reauths_after_unauthorized_response(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    client.access_token = "expired-token"
    responses = iter(
        [
            _ResponseStub(401, {"return_code": 401}),
            _ResponseStub(200, {"return_code": 0}),
        ]
    )
    auth_calls: list[str] = []

    monkeypatch.setattr(client.session, "request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(client, "auth", lambda: auth_calls.append("auth") or "new-token")
    monkeypatch.setattr(kiwoom_module.time, "sleep", lambda *_args, **_kwargs: None)

    body = client._request("POST", "/api/test", api_id="test", json={})

    assert body == {"return_code": 0}
    assert auth_calls == ["auth"]
