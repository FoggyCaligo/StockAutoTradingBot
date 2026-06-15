from dataclasses import dataclass, field
from datetime import datetime

from Daily_bot.models import Fill
from Daily_bot.risk.force_sell import _get_ticker, force_sell


@dataclass
class _Position:
    ticker: str
    quantity: int


@dataclass
class _RecorderStub:
    orders: list = field(default_factory=list)
    fills: list = field(default_factory=list)

    def save_order(self, order) -> None:
        self.orders.append(order)

    def save_fill(self, fill, side, source) -> None:
        self.fills.append((fill, side, source))


class _ClientStub:
    def __init__(self):
        self._open_orders = [
            {"ord_no": "1234567", "stk_cd": "A005930", "oso_qty": "3"},
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

    def sell_limit(self, ticker: str, quantity: int, price: int):
        self.sell_limit_calls.append((ticker, quantity, price))
        return type(
            "Order",
            (),
            {"order_id": f"OID-{ticker}", "ticker": ticker, "side": "SELL", "quantity": quantity, "price": price, "status": "SUBMITTED", "raw": {}},
        )()

    def sell_market(self, ticker: str, quantity: int):
        self.sell_market_calls.append((ticker, quantity))
        price = self.current_prices.get(ticker, 0)
        return type(
            "Order",
            (),
            {"order_id": f"OID-M-{ticker}", "ticker": ticker, "side": "SELL", "quantity": quantity, "price": price, "status": "SUBMITTED", "raw": {}},
        )()

    def get_order_fill(self, order_id: str):
        ticker = order_id.split("-")[-1]
        price = self.current_prices.get(ticker, 0)
        return Fill(order_id=order_id, ticker=ticker, quantity=1, price=price, filled_at=datetime.now())


def test_force_sell_passes_ticker_and_quantity_to_cancel_order():
    client = _ClientStub()
    recorder = _RecorderStub()

    force_sell(client, recorder=recorder)

    assert client.cancel_calls == [
        ("1234567", "005930", 3),
        ("MOCK-SELL-1", "000660", 2),
    ]
    assert client.sell_limit_calls == []
    assert client.sell_market_calls == [("005930", 3), ("000660", 1)]

    # Verify fills were recorded
    assert len(recorder.fills) == 2
    for fill, side, source in recorder.fills:
        assert side == "SELL"
        assert source == "force_sell"
        assert fill.order_id.startswith("OID-M-")
        assert fill.price > 0


def test_force_sell_get_ticker_normalizes_a_prefixed_codes():
    assert _get_ticker({"stk_cd": "A005930"}) == "005930"
