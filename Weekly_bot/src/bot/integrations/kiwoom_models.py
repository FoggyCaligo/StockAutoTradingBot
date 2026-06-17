from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class HogaLevel:
    price: int
    volume: int


@dataclass
class HogaSnapshot:
    ticker: str
    current_price: int
    bids: list[HogaLevel] = field(default_factory=list)
    asks: list[HogaLevel] = field(default_factory=list)
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
