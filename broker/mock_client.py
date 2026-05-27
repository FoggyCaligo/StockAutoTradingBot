from __future__ import annotations

import itertools
import random
from datetime import datetime

from models import Fill, HogaLevel, HogaSnapshot, OrderResult, Position
from utils import get_tick_size


class MockKiwoomClient:
    """Dry-run mock broker for development without real orders."""

    def __init__(self):
        self._order_seq = itertools.count(1)
        self.positions: dict[str, Position] = {}
        self.open_orders: dict[str, OrderResult] = {}

    def auth(self) -> str:
        return "mock-token"

    def get_20hoga(self, ticker: str) -> HogaSnapshot:
        base = random.randrange(10000, 50000, 50)
        tick = get_tick_size(base)
        bids = [HogaLevel(base - tick * i, random.randint(100, 3000)) for i in range(1, 21)]
        asks = [HogaLevel(base + tick * i, random.randint(100, 3000)) for i in range(1, 21)]
        return HogaSnapshot(ticker=ticker, current_price=base, bids=bids, asks=asks, captured_at=datetime.now())

    def buy_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        oid = f"MOCK-BUY-{next(self._order_seq)}"
        self.positions[ticker] = Position(ticker=ticker, quantity=quantity, avg_price=price)
        return OrderResult(order_id=oid, ticker=ticker, side="BUY", quantity=quantity, price=price, status="FILLED")

    def sell_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        oid = f"MOCK-SELL-{next(self._order_seq)}"
        self.open_orders[oid] = OrderResult(order_id=oid, ticker=ticker, side="SELL", quantity=quantity, price=price)
        return self.open_orders[oid]

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        oid = f"MOCK-MARKET-SELL-{next(self._order_seq)}"
        self.positions.pop(ticker, None)
        return OrderResult(order_id=oid, ticker=ticker, side="SELL", quantity=quantity, status="FILLED")

    def cancel_order(self, order_id: str) -> None:
        self.open_orders.pop(order_id, None)

    def get_positions(self) -> list[Position]:
        return list(self.positions.values())

    def get_open_orders(self) -> list[dict]:
        return [o.__dict__ for o in self.open_orders.values()]

    def wait_buy_filled(self, order_id: str, timeout_seconds: int = 30) -> Fill | None:
        # Mock assumes buy_limit fills immediately.
        return Fill(order_id=order_id, ticker="MOCK", quantity=1, price=10000)
