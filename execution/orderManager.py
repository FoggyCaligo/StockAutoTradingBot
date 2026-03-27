from broker.base import BrokerPort
from core.models import OrderRequest, OrderResult, Signal


class OrderManager:
    def __init__(self, broker: BrokerPort) -> None:
        self.broker = broker

    async def buyMarket(self, signal: Signal, quantity: int) -> OrderResult:
        orderRequest = OrderRequest(
            symbol=signal.symbol,
            side="buy",
            quantity=quantity,
            orderType="market",
            price=None,
        )
        return await self.broker.placeOrder(orderRequest)

    async def sellMarket(self, symbol: str, quantity: int) -> OrderResult:
        orderRequest = OrderRequest(
            symbol=symbol,
            side="sell",
            quantity=quantity,
            orderType="market",
            price=None,
        )
        return await self.broker.placeOrder(orderRequest)