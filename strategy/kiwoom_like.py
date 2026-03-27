from core.models import OrderBookSnapshot, Prediction


def buildCombinedLadder(orderbook: OrderBookSnapshot) -> tuple[list[int], list[int]]:
    bidPricesFarToNear = [level.price for level in reversed(orderbook.bidLevels)]
    bidSizesFarToNear = [level.quantity for level in reversed(orderbook.bidLevels)]

    askPricesNearToFar = [level.price for level in orderbook.askLevels]
    askSizesNearToFar = [level.quantity for level in orderbook.askLevels]

    combinedPrices = bidPricesFarToNear + askPricesNearToFar
    combinedSizes = bidSizesFarToNear + askSizesNearToFar
    return combinedPrices, combinedSizes


def predictPriceIndex(combinedSizes: list[int]) -> int:
    working = [max(0, int(x)) for x in combinedSizes]
    if not working:
        return 0

    middle = len(working) // 2
    buyIdx = middle - 1
    sellIdx = middle

    while True:
        if buyIdx < 0 or sellIdx >= len(working):
            break

        buyQty = working[buyIdx]
        sellQty = working[sellIdx]

        if buyQty > sellQty:
            working[buyIdx] -= sellQty
            sellIdx += 1
        elif buyQty < sellQty:
            working[sellIdx] -= buyQty
            buyIdx -= 1
        else:
            buyIdx -= 1
            sellIdx += 1

    resultIdx = (buyIdx + sellIdx) // 2
    if resultIdx < 0:
        return 0
    if resultIdx >= len(working):
        return len(working) - 1
    return resultIdx


def predictFromOrderbook(orderbook: OrderBookSnapshot) -> Prediction:
    combinedPrices, combinedSizes = buildCombinedLadder(orderbook)
    idx = predictPriceIndex(combinedSizes)

    entryPrice = orderbook.bestAsk
    predictedPrice = abs(int(combinedPrices[idx]))

    if entryPrice <= 0:
        expectedReturn = 0.0
    else:
        expectedReturn = (predictedPrice - entryPrice) / entryPrice

    return Prediction(
        symbol=orderbook.symbol,
        entryPrice=entryPrice,
        predictedPrice=predictedPrice,
        expectedReturn=expectedReturn,
        predictedIndex=idx,
    )