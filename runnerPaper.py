import asyncio
from collections import defaultdict

from broker.kisAuth import KisAuth
from broker.kisRest import KisRestBroker
from config.settings import settings
from execution.riskManager import RiskManager
from storage.sqliteRepo import SqliteRepo
from strategy.scorer import buildSignal
from strategy.selector import selectTopSignals


async def main() -> None:
    auth = KisAuth()
    broker = KisRestBroker(auth)
    repo = SqliteRepo()
    riskManager = RiskManager(maxDailyLoss=settings.maxDailyLoss)

    symbols = [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "035420",  # NAVER
        "005380",  # 현대차
        "035720",  # 카카오
    ]

    recentPricesMap: dict[str, list[int]] = defaultdict(list)
    positions = []

    try:
        while True:
            signals = []

            for symbol in symbols:
                orderBook = await broker.getOrderBook(symbol)
                recentPricesMap[symbol].append(orderBook.lastPrice)
                recentPricesMap[symbol] = recentPricesMap[symbol][-20:]

                signal = buildSignal(
                    orderBook=orderBook,
                    recentPrices=recentPricesMap[symbol],
                    quantity=1,
                    entryThreshold=settings.entryThreshold,
                    maxSpreadRatio=settings.maxSpreadRatio,
                )
                repo.saveSignal(signal)
                signals.append(signal)

            selectedSignals = selectTopSignals(signals, settings.topK)

            for signal in selectedSignals:
                canEnter, reason = riskManager.canEnter(signal, positions)
                print(signal.symbol, round(signal.finalScore, 6), reason)

            print("-" * 60)
            await asyncio.sleep(1)

    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())