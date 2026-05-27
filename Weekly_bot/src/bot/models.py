from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketSnapshot:
    code: str
    name: str
    is_kospi200: bool
    market_cap_krw: int
    current_price: int
    change_pct: float
    turnover_krw: int
    volume: int
    ma20: float
    ma30: float
    ma30_prev: float
    ma50: float
    ma50_prev: float
    ma120: float
    ma120_prev: float
    bid_price_1: int
    ask_price_1: int
    tick_size: int

    @property
    def spread_ticks(self) -> float:
        if self.tick_size <= 0:
            return float("inf")
        return (self.ask_price_1 - self.bid_price_1) / self.tick_size


@dataclass(frozen=True)
class Candidate:
    snapshot: MarketSnapshot
    score: float
    reasons: list[str]


@dataclass(frozen=True)
class OrderIntent:
    code: str
    name: str
    side: str
    quantity: int
    order_type: str
    reason: str
    reference_price: int


@dataclass(frozen=True)
class OrderExecutionResult:
    order_id: str
    code: str
    side: str
    requested_quantity: int
    status: str
    filled_quantity: int = 0
    fill_price: float = 0.0
    message: str = ""
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class Position:
    code: str
    name: str
    quantity: int
    avg_price: float
    entry_time: datetime | None = None

    def pnl_pct(self, current_price: float) -> float:
        if self.avg_price <= 0:
            return 0.0
        return (current_price / self.avg_price - 1.0) * 100.0


@dataclass(frozen=True)
class ExitDecision:
    position: Position
    current_price: int
    should_sell: bool
    reason: str
    pnl_pct: float
