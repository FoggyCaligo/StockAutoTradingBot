from dataclasses import dataclass

from Daily_bot.models import HogaLevel, HogaSnapshot
from Daily_bot.risk.stop_loss import is_stop_loss_triggered, monitor_stop_loss


@dataclass
class _Position:
    ticker: str
    quantity: int
    avg_price: int


@dataclass
class _RecorderStub:
    orders: list

    def save_order(self, order) -> None:
        self.orders.append(order)


class _ClientStub:
    def __init__(self):
        self.cancel_calls = []
        self.market_sell_calls = []
        self._open_orders = [{"ord_no": "1234567", "stk_cd": "005930", "oso_qty": "3"}]

    def get_20hoga(self, ticker: str) -> HogaSnapshot:
        return HogaSnapshot(
            ticker=ticker,
            current_price=9800,
            bids=[HogaLevel(9750, 100)],
            asks=[HogaLevel(9800, 100)],
        )

    def cancel_order(self, order_id: str, ticker: str = "", quantity: int = 0) -> None:
        self.cancel_calls.append((order_id, ticker, quantity))
        self._open_orders = []

    def get_open_orders(self):
        return self._open_orders

    def sell_market(self, ticker: str, quantity: int):
        self.market_sell_calls.append((ticker, quantity))
        return type(
            "Order",
            (),
            {"order_id": "MSELL-1", "ticker": ticker, "side": "SELL", "quantity": quantity, "price": 0, "status": "FILLED", "raw": None},
        )()


def test_is_stop_loss_triggered_when_price_falls_two_percent():
    position = _Position(ticker="005930", quantity=3, avg_price=10000)

    assert is_stop_loss_triggered(position, current_price=9800, stop_loss_percent=2.0) is True
    assert is_stop_loss_triggered(position, current_price=9801, stop_loss_percent=2.0) is False


def test_monitor_stop_loss_cancels_existing_orders_then_market_sells():
    client = _ClientStub()
    recorder = _RecorderStub(orders=[])
    positions = [_Position(ticker="005930", quantity=3, avg_price=10000)]
    open_orders = client.get_open_orders()
    cfg = {"risk": {"stop_loss_percent": 2.0}}

    triggered = monitor_stop_loss(client, recorder, positions, open_orders, cfg)

    assert triggered is True
    assert client.cancel_calls == [("1234567", "005930", 3)]
    assert client.market_sell_calls == [("005930", 3)]
    assert len(recorder.orders) == 1
