from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class BotState(str, Enum):
    NO_POSITION = "NO_POSITION"
    SCANNING = "SCANNING"
    BUYING = "BUYING"
    SELLING = "SELLING"
    FORCE_SELLING = "FORCE_SELLING"
    STOPPED = "STOPPED"


@dataclass
class Candidate:
    ticker: str
    name: str | None = None
    price: int = 0
    prev_close_price: int = 0
    expect_price: int = 0
    expect_revenue_percent: float = 0.0
    spread_percent: float = 0.0
    ask_depth_5_amount_krw: int = 0
    prev_day_change_percent: float = 0.0
    market_cap: int | None = None
    trading_value: int | None = None
    trend_ok: bool = False
    hoga_snapshot_time: datetime | None = None
    raw_hoga: dict[str, Any] | None = None


@dataclass
class HogaLevel:
    price: int
    volume: int


@dataclass
class HogaSnapshot:
    ticker: str
    current_price: int
    bids: list[HogaLevel] = field(default_factory=list)  # 매수호가: 높은 가격부터
    asks: list[HogaLevel] = field(default_factory=list)  # 매도호가: 낮은 가격부터
    captured_at: datetime = field(default_factory=datetime.now)
    raw: dict[str, Any] | None = None


@dataclass
class OrderResult:
    order_id: str
    ticker: str
    side: str
    quantity: int
    price: int | None = None
    status: str = "SUBMITTED"
    raw: dict[str, Any] | None = None


@dataclass
class Fill:
    order_id: str
    ticker: str
    quantity: int
    price: int
    filled_at: datetime = field(default_factory=datetime.now)
    raw: dict[str, Any] | None = None


@dataclass
class Position:
    ticker: str
    quantity: int
    avg_price: int
    raw: dict[str, Any] | None = None
