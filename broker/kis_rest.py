import json
from datetime import datetime

import httpx

from core.models import OrderBookSnapshot, QuoteLevel


class KisRestBroker:
    def __init__(
        self,
        appKey: str,
        appSecret: str,
        accessToken: str,
        accountNo: str,
        productCode: str,
        baseUrl: str,
    ) -> None:
        self.appKey = appKey
        self.appSecret = appSecret
        self.accessToken = accessToken
        self.accountNo = accountNo.replace("-", "")[:8]
        self.productCode = productCode
        self.baseUrl = baseUrl.rstrip("/")

        self.client = httpx.AsyncClient(timeout=10.0)

    def _headers(self, trId: str) -> dict:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.accessToken}",
            "appkey": self.appKey,
            "appsecret": self.appSecret,
            "tr_id": trId,
            "custtype": "P",
        }

    async def close(self) -> None:
        await self.client.aclose()

    async def getOrderbook(self, symbol: str) -> OrderBookSnapshot:
        url = f"{self.baseUrl}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }

        response = await self.client.get(
            url,
            headers=self._headers("FHKST01010200"),
            params=params,
        )

        text = response.text
        if response.status_code != 200:
            raise RuntimeError(
                f"호가 조회 실패: status={response.status_code}, symbol={symbol}, body={text[:500]}"
            )

        data = response.json()

        if data.get("rt_cd") != "0":
            raise RuntimeError(
                f"호가 조회 실패: symbol={symbol}, rt_cd={data.get('rt_cd')}, "
                f"msg_cd={data.get('msg_cd')}, msg1={data.get('msg1')}"
            )

        output = data.get("output1") or data.get("output")
        if not output:
            raise RuntimeError(f"호가 응답 구조 이상: {data}")

        askLevels = []
        bidLevels = []

        for i in range(1, 11):
            askLevels.append(
                QuoteLevel(
                    price=int(output.get(f"askp{i}", 0) or 0),
                    quantity=int(output.get(f"askp_rsqn{i}", 0) or 0),
                )
            )
            bidLevels.append(
                QuoteLevel(
                    price=int(output.get(f"bidp{i}", 0) or 0),
                    quantity=int(output.get(f"bidp_rsqn{i}", 0) or 0),
                )
            )

        return OrderBookSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            bidLevels=bidLevels,
            askLevels=askLevels,
        )