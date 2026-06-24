from dataclasses import dataclass, field
from datetime import datetime

from Daily_bot.models import Fill, HogaLevel, HogaSnapshot
from Daily_bot.main import _build_exit_plan_metadata
from Daily_bot.risk.stop_loss import (
    get_planned_stop_loss_price,
    get_position_loss_percent,
    get_stop_loss_limit_price,
    get_stop_loss_reference_price,
    is_stop_loss_triggered_by_price,
    is_stop_loss_triggered,
    monitor_stop_loss,
)


@dataclass
class _Position:
    ticker: str
    quantity: int
    avg_price: int


@dataclass
class _RecorderStub:
    orders: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    planned_stop_loss_by_ticker: dict[str, int] = field(default_factory=dict)

    def save_order(self, order) -> None:
        self.orders.append(order)

    def save_fill(self, fill, side, source) -> None:
        self.fills.append((fill, side, source))

    def get_latest_planned_stop_loss_price(self, ticker: str) -> int:
        return self.planned_stop_loss_by_ticker.get(ticker, 0)


class _ClientStub:
    def __init__(self):
        self.cancel_calls = []
        self.limit_sell_calls = []
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

    def sell_limit(self, ticker: str, quantity: int, price: int):
        self.limit_sell_calls.append((ticker, quantity, price))
        return type(
            "Order",
            (),
            {"order_id": f"LSELL-{ticker}", "ticker": ticker, "side": "SELL", "quantity": quantity, "price": price, "status": "FILLED", "raw": {}},
        )()

    def get_order_fill(self, order_id: str):
        ticker = order_id.split("-")[-1]
        return Fill(order_id=order_id, ticker=ticker, quantity=1, price=self.current_price, filled_at=datetime.now())


def test_is_stop_loss_triggered_when_price_falls_two_percent():
    position = _Position(ticker="005930", quantity=3, avg_price=10000)

    assert is_stop_loss_triggered(position, current_price=9800, stop_loss_percent=2.0) is True
    assert is_stop_loss_triggered(position, current_price=9801, stop_loss_percent=2.0) is False


def test_is_stop_loss_triggered_by_price_uses_planned_stop_loss_price():
    assert is_stop_loss_triggered_by_price(current_price=9700, stop_loss_price=9700) is True
    assert is_stop_loss_triggered_by_price(current_price=9701, stop_loss_price=9700) is False


def test_build_exit_plan_metadata_uses_expected_tick_distance_times_multiplier():
    candidate = type("Candidate", (), {"expect_price": 10_200})()

    metadata = _build_exit_plan_metadata(
        candidate=candidate,
        buy_reference_price=10_000,
        target_price=10_150,
        stop_loss_tick_count=0,
        stop_loss_tick_multiplier=2.0,
    )

    assert metadata["planned_profit_tick_distance"] == 4
    assert metadata["planned_stop_loss_tick_distance"] == 8
    assert metadata["planned_stop_loss_price"] == 9_920


def test_build_exit_plan_metadata_uses_minimum_tick_count_when_dynamic_distance_is_smaller():
    candidate = type("Candidate", (), {"expect_price": 10_200})()

    metadata = _build_exit_plan_metadata(
        candidate=candidate,
        buy_reference_price=10_000,
        target_price=10_150,
        stop_loss_tick_count=5,
        stop_loss_tick_multiplier=1.0,
    )

    assert metadata["planned_profit_tick_distance"] == 4
    assert metadata["planned_stop_loss_tick_distance"] == 5
    assert metadata["planned_stop_loss_price"] == 9_950


def test_build_exit_plan_metadata_uses_greater_of_minimum_ticks_and_expected_distance():
    candidate = type("Candidate", (), {"expect_price": 10_300})()

    metadata = _build_exit_plan_metadata(
        candidate=candidate,
        buy_reference_price=10_000,
        target_price=10_250,
        stop_loss_tick_count=5,
        stop_loss_tick_multiplier=1.0,
    )

    assert metadata["planned_profit_tick_distance"] == 6
    assert metadata["planned_stop_loss_tick_distance"] == 6
    assert metadata["planned_stop_loss_price"] == 9_940


def test_build_exit_plan_metadata_disables_planned_stop_loss_when_multiplier_is_zero():
    candidate = type("Candidate", (), {"expect_price": 10_200})()

    metadata = _build_exit_plan_metadata(
        candidate=candidate,
        buy_reference_price=10_000,
        target_price=10_150,
        stop_loss_tick_count=0,
        stop_loss_tick_multiplier=0.0,
    )

    assert metadata["planned_stop_loss_tick_distance"] == 0
    assert metadata["planned_stop_loss_price"] == 0


def test_get_planned_stop_loss_price_returns_zero_when_recorder_has_no_method():
    assert get_planned_stop_loss_price(object(), "005930") == 0


def test_get_stop_loss_reference_price_prefers_best_bid():
    snapshot = HogaSnapshot(
        ticker="005930",
        current_price=9850,
        bids=[HogaLevel(9790, 100)],
        asks=[HogaLevel(9850, 100)],
    )

    assert get_stop_loss_reference_price(snapshot) == 9790


def test_get_stop_loss_limit_price_uses_best_bid():
    snapshot = HogaSnapshot(
        ticker="005930",
        current_price=9850,
        bids=[HogaLevel(9790, 100)],
        asks=[HogaLevel(9850, 100)],
    )

    assert get_stop_loss_limit_price(snapshot) == 9790


def test_get_position_loss_percent_prefers_position_raw_profit_rate():
    position = _Position(ticker="005930", quantity=3, avg_price=10000)
    position.raw = {"prft_rt": "-5.12"}

    assert get_position_loss_percent(position) == -5.12


def test_monitor_stop_loss_cancels_existing_orders_then_limit_sells():
    client = _ClientStub()
    recorder = _RecorderStub(planned_stop_loss_by_ticker={"005930": 9_800})
    positions = [_Position(ticker="005930", quantity=3, avg_price=10000)]
    open_orders = client.get_open_orders()
    cfg = {"risk": {"stop_loss_percent": 2.0}}

    triggered = monitor_stop_loss(client, recorder, positions, open_orders, cfg)

    assert triggered is True
    assert client.cancel_calls == [("1234567", "005930", 3)]
    assert client.limit_sell_calls == [("005930", 3, 9750)]
    assert len(recorder.orders) == 1
    
    # Verify fill was recorded
    assert len(recorder.fills) == 1
    fill, side, source = recorder.fills[0]
    assert side == "SELL"
    assert source == "stop_loss"
    assert fill.ticker == "005930"
    assert fill.price == 9800


def test_monitor_stop_loss_triggers_on_best_bid_even_if_last_price_is_higher():
    client = _ClientStub()
    client.current_price = 9850
    recorder = _RecorderStub(planned_stop_loss_by_ticker={"005930": 9_790})
    positions = [_Position(ticker="005930", quantity=3, avg_price=10000)]
    open_orders = client.get_open_orders()
    cfg = {"risk": {"stop_loss_percent": 2.0}}

    original_get_20hoga = client.get_20hoga

    def _snapshot_with_lower_bid(ticker: str):
        snapshot = original_get_20hoga(ticker)
        snapshot.bids = [HogaLevel(9790, 100)]
        snapshot.asks = [HogaLevel(9850, 100)]
        return snapshot

    client.get_20hoga = _snapshot_with_lower_bid

    triggered = monitor_stop_loss(client, recorder, positions, open_orders, cfg)

    assert triggered is True
    assert client.limit_sell_calls == [("005930", 3, 9790)]


def test_monitor_stop_loss_triggers_from_account_snapshot_loss_rate_without_hoga_drop():
    client = _ClientStub()
    client.current_price = 9990
    recorder = _RecorderStub()
    position = _Position(ticker="005930", quantity=3, avg_price=10000)
    position.raw = {"prft_rt": "-5.10", "cur_prc": "9990"}
    open_orders = client.get_open_orders()
    cfg = {"risk": {"stop_loss_percent": 4.0}}

    triggered = monitor_stop_loss(client, recorder, [position], open_orders, cfg)

    assert triggered is True
    assert client.limit_sell_calls == [("005930", 3, 9750)]
