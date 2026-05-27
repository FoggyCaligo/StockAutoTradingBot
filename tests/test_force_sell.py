from dataclasses import dataclass

from risk.force_sell import force_sell


@dataclass
class _Position:
    ticker: str
    quantity: int


class _ClientStub:
    def __init__(self):
        self._open_orders = [
            {"ord_no": "1234567", "stk_cd": "005930", "oso_qty": "3"},
            {"order_id": "MOCK-SELL-1", "ticker": "000660", "ord_qty": 2},
        ]
        self._positions = [_Position("005930", 3), _Position("000660", 1)]
        self.cancel_calls = []
        self.sell_calls = []

    def get_open_orders(self):
        return self._open_orders

    def cancel_order(self, order_id: str, ticker: str = "", quantity: int = 0):
        self.cancel_calls.append((order_id, ticker, quantity))
        self._open_orders = []

    def get_positions(self):
        return self._positions

    def sell_market(self, ticker: str, quantity: int):
        self.sell_calls.append((ticker, quantity))


def test_force_sell_passes_ticker_and_quantity_to_cancel_order():
    client = _ClientStub()

    force_sell(client)

    assert client.cancel_calls == [
        ("1234567", "005930", 3),
        ("MOCK-SELL-1", "000660", 2),
    ]
    assert client.sell_calls == [("005930", 3), ("000660", 1)]
