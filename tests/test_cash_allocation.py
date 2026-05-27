from dataclasses import dataclass

from main import activate_buy
from models import Candidate, Fill, OrderResult


@dataclass
class _RecorderStub:
    orders: list[OrderResult]

    def save_order(self, order: OrderResult) -> None:
        self.orders.append(order)


class _ClientStub:
    def __init__(self, orderable_cash: int):
        self.orderable_cash = orderable_cash
        self.buy_calls: list[tuple[str, int, int]] = []
        self.sell_calls: list[tuple[str, int, int]] = []
        self.market_sell_calls: list[tuple[str, int]] = []

    def get_orderable_cash(self) -> int:
        return self.orderable_cash

    def buy_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        self.buy_calls.append((ticker, quantity, price))
        return OrderResult(order_id=f"BUY-{len(self.buy_calls)}", ticker=ticker, side="BUY", quantity=quantity, price=price, status="SUBMITTED")

    def wait_buy_filled(self, order_id: str, timeout_seconds: int = 30) -> Fill | None:
        return Fill(order_id=order_id, ticker="MOCK", quantity=1, price=10000)

    def sell_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        self.sell_calls.append((ticker, quantity, price))
        return OrderResult(order_id=f"SELL-{len(self.sell_calls)}", ticker=ticker, side="SELL", quantity=quantity, price=price, status="SUBMITTED")

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        self.market_sell_calls.append((ticker, quantity))
        return OrderResult(order_id=f"MSELL-{len(self.market_sell_calls)}", ticker=ticker, side="SELL", quantity=quantity, price=0, status="FILLED")


def _cfg() -> dict:
    return {
        "api": {"order_rate_limit_per_second": 1000},
        "risk": {
            "max_budget_per_stock_krw": 100_000,
            "max_budget_per_cycle_krw": 150_000,
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
