from datetime import datetime

from core.models import OrderBookSnapshot, QuoteLevel


class KisParsers:
    @staticmethod
    def parseOrderbookResponse(symbol: str, data: dict) -> OrderBookSnapshot:
        # TODO: 한국투자 실제 응답 필드명에 맞춰 수정
        output = data.get("output", {})

        askLevels = [
            QuoteLevel(price=int(output[f"askp{i}"]), quantity=int(output[f"askp_rsqn{i}"]))
            for i in range(1, 11)
        ]
        bidLevels = [
            QuoteLevel(price=int(output[f"bidp{i}"]), quantity=int(output[f"bidp_rsqn{i}"]))
            for i in range(1, 11)
        ]

        lastPrice = int(output.get("stck_prpr", askLevels[0].price))
        return OrderBookSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            askLevels=askLevels,
            bidLevels=bidLevels,
            lastPrice=lastPrice,
        )