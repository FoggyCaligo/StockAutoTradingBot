from core.models import OrderBookSnapshot, Signal
from strategy.features import calcImpactPenalty, calcSpreadRatio, calcVolatilityPenalty
from strategy.predictPrice import predictPrice


def buildSignal(
    orderBook: OrderBookSnapshot,
    recentPrices: list[int],
    quantity: int,
    entryThreshold: float,
    maxSpreadRatio: float,
) -> Signal:
    predictedPrice = predictPrice(orderBook)
    estimatedEntryPrice = orderBook.ask1

    rawEdge = (predictedPrice - estimatedEntryPrice) / max(estimatedEntryPrice, 1)
    spreadRatio = calcSpreadRatio(orderBook)
    impactPenalty = calcImpactPenalty(orderBook, quantity)
    volatilityPenalty = calcVolatilityPenalty(recentPrices)

    finalScore = rawEdge - spreadRatio - impactPenalty - volatilityPenalty

    isEntryCandidate = (
        rawEdge >= entryThreshold
        and spreadRatio <= maxSpreadRatio
        and finalScore > 0
    )

    reason = (
        f"rawEdge={rawEdge:.4f}, spread={spreadRatio:.4f}, "
        f"impact={impactPenalty:.4f}, vol={volatilityPenalty:.4f}"
    )

    return Signal(
        symbol=orderBook.symbol,
        predictedPrice=predictedPrice,
        rawEdge=rawEdge,
        spreadRatio=spreadRatio,
        impactPenalty=impactPenalty,
        volatilityPenalty=volatilityPenalty,
        finalScore=finalScore,
        isEntryCandidate=isEntryCandidate,
        reason=reason,
    )