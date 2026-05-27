import broker.kiwoom_client as kiwoom_module
from broker.kiwoom_client import KiwoomClient


def test_wait_buy_filled_requires_full_quantity(monkeypatch):
    client = KiwoomClient(base_url="https://example.com")
    responses = [
        {
            "cntr": [
                {"stk_cd": "A005930", "cntr_qty": "3", "cntr_pric": "10000"},
            ]
        },
        {
            "cntr": [
                {"stk_cd": "A005930", "cntr_qty": "3", "cntr_pric": "10000"},
                {"stk_cd": "A005930", "cntr_qty": "7", "cntr_pric": "10050"},
            ]
        },
    ]
    monotonic_values = iter([0.0, 0.1, 0.2])

    monkeypatch.setattr(client, "get_order_status", lambda order_id: responses.pop(0))
    monkeypatch.setattr(kiwoom_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(kiwoom_module.time, "monotonic", lambda: next(monotonic_values))

    fill = client.wait_buy_filled("BUY-1", expected_quantity=10, timeout_seconds=1)

    assert fill is not None
    assert fill.ticker == "005930"
    assert fill.quantity == 10
    assert fill.price == 10050
