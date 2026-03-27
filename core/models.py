from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class QuoteLevel:
    price: int
    quantity: int


@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    bidLevels: list[QuoteLevel]   # bid1 ~ bid10
    askLevels: list[QuoteLevel]   # ask1 ~ ask10

    @property
    def bestBid(self) -> int:
        return self.bidLevels[0].price

    @property
    def bestAsk(self) -> int:
        return self.askLevels[0].price


@dataclass(frozen=True)
class Prediction:
    symbol: str
    entryPrice: int
    predictedPrice: int
    expectedReturn: float
    predictedIndex: int


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    buyPrice: int
    sellPrice: int
    quantity: int
    expectedReturn: float