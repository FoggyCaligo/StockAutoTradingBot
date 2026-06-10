import Daily_bot.broker.kiwoom_client as kiwoom_module
from Daily_bot.broker.kiwoom_client import KiwoomClient


def test_get_orderable_cash_prefers_stock_buying_power(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    response = {
        "elwdpst_evlta": "000000000211315",
        "pymn_alow_amt": "000000000024449",
        "ord_alow_amt": "000000000024449",
        "100stk_ord_alow_amt": "000000000211315",
        "return_code": 0,
    }
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    result = client.get_orderable_cash()

    assert result == 211315


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
