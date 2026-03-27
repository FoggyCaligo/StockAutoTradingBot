from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class QuoteLevel:
    price: int
    quantity: int


@dataclass
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    askLevels: list[QuoteLevel]
    bidLevels: list[QuoteLevel]
    lastPrice: int

    @property
    def ask1(self) -> int:
        return self.askLevels[0].price

    @property
    def bid1(self) -> int:
        return self.bidLevels[0].price

    @property
    def midPrice(self) -> float:
        return (self.ask1 + self.bid1) / 2.0


@dataclass
class Signal:
    symbol: str
    predictedPrice: int
    rawEdge: float
    spreadRatio: float
    impactPenalty: float
    volatilityPenalty: float
    finalScore: float
    isEntryCandidate: bool
    reason: str


@dataclass
class Position:
    symbol: str
    quantity: int
    averagePrice: int
    openedAt: datetime
    lastSignalScore: float = 0.0


@dataclass
class OrderRequest:
    symbol: str
    side: str
    quantity: int
    orderType: str
    price: Optional[int] = None


@dataclass
class OrderResult:
    success: bool
    orderId: str
    message: str
    raw: dict = field(default_factory=dict)