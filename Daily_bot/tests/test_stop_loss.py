from dataclasses import dataclass, field
from datetime import datetime

from Daily_bot.models import Fill, HogaLevel, HogaSnapshot
from Daily_bot.risk.stop_loss import is_stop_loss_triggered, monitor_stop_loss


@dataclass
class _Position:
    ticker: str
    quantity: int
    avg_price: int


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
        self.cancel_calls = []
        self.market_sell_calls = []
        self._open_orders = [{"ord_no": "1234567", "stk_cd": "005930", "oso_qty": "3"}]
        self.current_price = 9800

    def get_20hoga(self, ticker: str) -> HogaSnapshot:
        return HogaSnapshot(
            ticker=ticker,
            current_price=self.current_price,
            bids=[HogaLevel(9750, 100)],
            asks=[HogaLevel(self.current_price, 100)],
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
            {"order_id": f"MSELL-{ticker}", "ticker": ticker, "side": "SELL", "quantity": quantity, "price": 0, "status": "FILLED", "raw": {}},
        )()

    def get_order_fill(self, order_id: str):
        ticker = order_id.split("-")[-1]
        return Fill(order_id=order_id, ticker=ticker, quantity=1, price=self.current_price, filled_at=datetime.now())


def test_is_stop_loss_triggered_when_price_falls_two_percent():
    position = _Position(ticker="005930", quantity=3, avg_price=10000)

    assert is_stop_loss_triggered(position, current_price=9800, stop_loss_percent=2.0) is True
    assert is_stop_loss_triggered(position, current_price=9801, stop_loss_percent=2.0) is False


def test_monitor_stop_loss_cancels_existing_orders_then_market_sells():
    client = _ClientStub()
    recorder = _RecorderStub()
    positions = [_Position(ticker="005930", quantity=3, avg_price=10000)]
    open_orders = client.get_open_orders()
    cfg = {"risk": {"stop_loss_percent": 2.0}}

    triggered = monitor_stop_loss(client, recorder, positions, open_orders, cfg)

    assert triggered is True
    assert client.cancel_calls == [("1234567", "005930", 3)]
    assert client.market_sell_calls == [("005930", 3)]
    assert len(recorder.orders) == 1
    
    # Verify fill was recorded
    assert len(recorder.fills) == 1
    fill, side, source = recorder.fills[0]
    assert side == "SELL"
    assert source == "stop_loss"
    assert fill.ticker == "005930"
    assert fill.price == 9800
