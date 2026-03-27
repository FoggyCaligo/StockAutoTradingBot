from core.models import OrderBookSnapshot


def calcSpreadRatio(orderBook: OrderBookSnapshot) -> float:
    return (orderBook.ask1 - orderBook.bid1) / max(orderBook.midPrice, 1.0)


def calcImpactPenalty(orderBook: OrderBookSnapshot, quantity: int) -> float:
    remain = quantity
    totalCost = 0

    for level in orderBook.askLevels:
        if remain <= 0:
            break
        takeQty = min(remain, level.quantity)
        totalCost += takeQty * level.price
        remain -= takeQty

    if remain > 0:
        return 1.0

    averageFill = totalCost / max(quantity, 1)
    return max(0.0, (averageFill - orderBook.ask1) / max(orderBook.ask1, 1))


def calcVolatilityPenalty(recentPrices: list[int]) -> float:
    if len(recentPrices) < 2:
        return 0.0

    returns = []
    for prevPrice, nextPrice in zip(recentPrices[:-1], recentPrices[1:]):
        if prevPrice <= 0:
            continue
        returns.append((nextPrice - prevPrice) / prevPrice)

    if not returns:
        return 0.0

    meanValue = sum(returns) / len(returns)
    variance = sum((value - meanValue) ** 2 for value in returns) / len(returns)
    return variance ** 0.5