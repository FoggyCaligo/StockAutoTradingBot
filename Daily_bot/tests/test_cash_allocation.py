from dataclasses import dataclass

from Daily_bot.main import activate_buy
from Daily_bot.models import Candidate, Fill, OrderResult


@dataclass
class _RecorderStub:
    orders: list[OrderResult]

    def save_order(self, order: OrderResult) -> None:
        self.orders.append(order)


class _ClientStub:
    def __init__(self, orderable_cash: int):
        self.orderable_cash = orderable_cash
        self.orderable_cash_sequence: list[int] | None = None
        self.fill_sequence: list[Fill | None] | None = None
        self.buy_fill_sequence: list[Fill | None] | None = None
        self.sell_limit_error: RuntimeError | None = None
        self.positions = []
        self.open_orders = []
        self.buy_calls: list[tuple[str, int, int]] = []
        self.sell_calls: list[tuple[str, int, int]] = []
        self.market_sell_calls: list[tuple[str, int]] = []
        self.cancel_calls: list[tuple[str, str, int]] = []

    def get_orderable_cash(self) -> int:
        if self.orderable_cash_sequence:
            return self.orderable_cash_sequence.pop(0)
        return self.orderable_cash

    def buy_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        self.buy_calls.append((ticker, quantity, price))
        return OrderResult(order_id=f"BUY-{len(self.buy_calls)}", ticker=ticker, side="BUY", quantity=quantity, price=price, status="SUBMITTED")

    def wait_buy_filled(
        self,
        order_id: str,
        expected_quantity: int | None = None,
        timeout_seconds: int = 30,
    ) -> Fill | None:
        if self.fill_sequence:
            return self.fill_sequence.pop(0)
        return Fill(order_id=order_id, ticker="MOCK", quantity=1, price=10000)

    def get_buy_fill(self, order_id: str) -> Fill | None:
        if self.buy_fill_sequence:
            return self.buy_fill_sequence.pop(0)
        return None

    def cancel_order(self, order_id: str, ticker: str = "", quantity: int = 0) -> None:
        self.cancel_calls.append((order_id, ticker, quantity))

    def wait_until_order_cancelled(self, order_id: str, timeout_seconds: int = 30) -> bool:
        return True

    def sell_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        if self.sell_limit_error is not None:
            raise self.sell_limit_error
        self.sell_calls.append((ticker, quantity, price))
        return OrderResult(order_id=f"SELL-{len(self.sell_calls)}", ticker=ticker, side="SELL", quantity=quantity, price=price, status="SUBMITTED")

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        self.market_sell_calls.append((ticker, quantity))
        return OrderResult(order_id=f"MSELL-{len(self.market_sell_calls)}", ticker=ticker, side="SELL", quantity=quantity, price=0, status="FILLED")

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return self.open_orders


def _cfg() -> dict:
    return {
        "api": {"order_rate_limit_per_second": 1000},
        "risk": {
            "max_budget_per_stock_krw": 100_000,
            "max_budget_per_cycle_krw": 150_000,
        },
        "strategy": {"sell_tick_offset": 1},
    }


def _unlimited_cfg() -> dict:
    return {
        "api": {"order_rate_limit_per_second": 1000},
        "risk": {
            "max_budget_per_stock_krw": 0,
            "max_budget_per_cycle_krw": 0,
        },
        "strategy": {"sell_tick_offset": 1},
    }


def test_activate_buy_uses_orderable_cash_and_cycle_budget():
    client = _ClientStub(orderable_cash=120_000)
    recorder = _RecorderStub(orders=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _cfg())

    assert client.buy_calls == [
        ("005930", 10, 10_000),
        ("000660", 2, 10_000),
    ]


def test_activate_buy_skips_when_orderable_cash_is_zero():
    client = _ClientStub(orderable_cash=0)
    recorder = _RecorderStub(orders=[])
    targets = [Candidate(ticker="005930", price=10_000, expect_price=10_200)]

    activate_buy(client, recorder, targets, _cfg())

    assert client.buy_calls == []


def test_activate_buy_refreshes_cash_after_unfilled_buy_cancel():
    client = _ClientStub(orderable_cash=120_000)
    client.orderable_cash_sequence = [120_000, 120_000]
    client.fill_sequence = [
        None,
        Fill(order_id="BUY-2", ticker="000660", quantity=10, price=10_000),
    ]
    recorder = _RecorderStub(orders=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _cfg())

    assert client.cancel_calls == [("BUY-1", "005930", 10)]
    assert client.buy_calls == [
        ("005930", 10, 10_000),
        ("000660", 10, 10_000),
    ]


def test_activate_buy_places_exit_order_for_partial_fill_after_cancel():
    client = _ClientStub(orderable_cash=120_000)
    client.orderable_cash_sequence = [120_000]
    client.fill_sequence = [None]
    client.buy_fill_sequence = [Fill(order_id="BUY-1", ticker="005930", quantity=3, price=10_000)]
    recorder = _RecorderStub(orders=[])
    targets = [Candidate(ticker="005930", price=10_000, expect_price=10_200)]

    activate_buy(client, recorder, targets, _cfg())

    assert client.cancel_calls == [("BUY-1", "005930", 10)]
    assert client.sell_calls == [("005930", 3, 10150)]


def test_activate_buy_distributes_full_cash_across_all_targets_when_limits_removed():
    client = _ClientStub(orderable_cash=300_000)
    recorder = _RecorderStub(orders=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
        Candidate(ticker="035420", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _unlimited_cfg())

    assert client.buy_calls == [
        ("005930", 10, 10_000),
        ("000660", 10, 10_000),
        ("035420", 10, 10_000),
    ]


def test_activate_buy_stops_new_buys_after_exception_when_position_exists():
    client = _ClientStub(orderable_cash=300_000)
    client.fill_sequence = [Fill(order_id="BUY-1", ticker="005930", quantity=10, price=10_000)]
    client.sell_limit_error = RuntimeError("sell api failed")
    client.positions = [type("Position", (), {"ticker": "005930", "quantity": 10, "avg_price": 10_000})()]
    recorder = _RecorderStub(orders=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _unlimited_cfg())

    assert client.buy_calls == [("005930", 15, 10_000)]
