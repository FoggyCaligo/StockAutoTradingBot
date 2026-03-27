from typing import Protocol

from core.models import OrderBookSnapshot, OrderRequest, OrderResult, Position


class BrokerPort(Protocol):
    async def getOrderBook(self, symbol: str) -> OrderBookSnapshot:
        ...

    async def placeOrder(self, orderRequest: OrderRequest) -> OrderResult:
        ...

    async def getPositions(self) -> list[Position]:
        ...

    async def close(self) -> None:
        ...