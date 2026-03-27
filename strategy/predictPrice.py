from core.models import OrderBookSnapshot


def predictPrice(orderBook: OrderBookSnapshot) -> int:
    askLevels = orderBook.askLevels
    bidLevels = orderBook.bidLevels

    askTotal = sum(level.quantity for level in askLevels)
    bidTotal = sum(level.quantity for level in bidLevels)

    if askTotal == 0 and bidTotal == 0:
        return orderBook.lastPrice

    # 네 기존 발표 전략 취지를 살린 단순 버전
    # 나중에 기존 키움 코드의 상쇄 로직으로 교체
    imbalance = bidTotal - askTotal
    imbalanceRatio = imbalance / max(askTotal + bidTotal, 1)

    priceRange = max(1, askLevels[0].price - bidLevels[0].price)
    predictedMove = round(imbalanceRatio * priceRange * 5)

    predictedPrice = orderBook.midPrice + predictedMove
    return max(1, int(round(predictedPrice)))