import asyncio
from datetime import datetime

from broker.kis_rest import KisRestBroker
from config.settings import settings
from storage.sqlite_repo import SqliteRepo
from strategy.kiwoom_like import predictFromOrderbook
from strategy.selector import buildOrderPlans


async def scanOnce(broker: KisRestBroker, repo: SqliteRepo) -> None:
    predictions = []

    for symbol in settings.symbols:
        try:
            orderbook = await broker.getOrderbook(symbol)
            prediction = predictFromOrderbook(orderbook)
            predictions.append(prediction)
            repo.savePrediction(prediction)
        except Exception as exc:
            print(f"[WARN] {symbol} 스캔 실패: {exc}")

    predictions.sort(key=lambda x: x.expectedReturn, reverse=True)

    plans = buildOrderPlans(
        predictions=predictions,
        budget=settings.budget,
        topK=settings.topK,
        minExpectedReturn=settings.minExpectedReturn,
    )

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print("=== TOP CANDIDATES ===")
    for p in predictions[: settings.topK * 2]:
        print(
            f"{p.symbol} | entry={p.entryPrice} | pred={p.predictedPrice} "
            f"| ret={p.expectedReturn * 100:.3f}%"
        )

    print("=== ORDER PLANS ===")
    for plan in plans:
        print(
            f"{plan.symbol} | buy={plan.buyPrice} | sell={plan.sellPrice} "
            f"| qty={plan.quantity} | ret={plan.expectedReturn * 100:.3f}%"
        )
        repo.saveOrderPlan(plan)


async def main() -> None:
    broker = KisRestBroker(
        appKey=settings.kisAppKey,
        appSecret=settings.kisAppSecret,
        accessToken=settings.kisAccessToken,
        accountNo=settings.kisAccountNo,
        productCode=settings.kisProductCode,
        baseUrl=settings.kisBaseUrl,
    )
    repo = SqliteRepo()

    try:
        while True:
            await scanOnce(broker, repo)
            await asyncio.sleep(settings.scanIntervalSeconds)
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())