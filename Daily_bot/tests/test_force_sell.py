from dataclasses import dataclass

from Daily_bot.models import HogaSnapshot
from Daily_bot.risk.force_sell import force_sell


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
        self.sell_limit_calls = []
        self.sell_market_calls = []
        self.current_prices = {"005930": 71000, "000660": 189000}

    def get_open_orders(self):
        return self._open_orders

    def cancel_order(self, order_id: str, ticker: str = "", quantity: int = 0):
        self.cancel_calls.append((order_id, ticker, quantity))
        self._open_orders = []

    def get_positions(self):
        return self._positions

    def get_20hoga(self, ticker: str):
        return HogaSnapshot(ticker=ticker, current_price=self.current_prices[ticker])

    def sell_limit(self, ticker: str, quantity: int, price: int):
        self.sell_limit_calls.append((ticker, quantity, price))

    def sell_market(self, ticker: str, quantity: int):
        self.sell_market_calls.append((ticker, quantity))


def test_force_sell_passes_ticker_and_quantity_to_cancel_order():
    client = _ClientStub()

    force_sell(client)

    assert client.cancel_calls == [
        ("1234567", "005930", 3),
        ("MOCK-SELL-1", "000660", 2),
    ]
    assert client.sell_limit_calls == [("005930", 3, 71000), ("000660", 1, 189000)]
    assert client.sell_market_calls == []
