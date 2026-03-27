from core.models import Prediction, OrderPlan


def selectTopPredictions(
    predictions: list[Prediction],
    topK: int,
    minExpectedReturn: float,
) -> list[Prediction]:
    candidates = [
        p for p in predictions
        if p.expectedReturn >= minExpectedReturn
    ]
    candidates.sort(key=lambda x: x.expectedReturn, reverse=True)
    return candidates[:topK]


def buildOrderPlans(
    predictions: list[Prediction],
    budget: int,
    topK: int,
    minExpectedReturn: float,
) -> list[OrderPlan]:
    selected = selectTopPredictions(
        predictions=predictions,
        topK=topK,
        minExpectedReturn=minExpectedReturn,
    )

    if not selected:
        return []

    budgetPerSymbol = budget / topK
    plans: list[OrderPlan] = []

    for p in selected:
        if p.entryPrice <= 0:
            continue

        quantity = int(budgetPerSymbol // p.entryPrice)
        if quantity <= 0:
            continue

        plans.append(
            OrderPlan(
                symbol=p.symbol,
                buyPrice=p.entryPrice,
                sellPrice=p.predictedPrice,
                quantity=quantity,
                expectedReturn=p.expectedReturn,
            )
        )

    return plans