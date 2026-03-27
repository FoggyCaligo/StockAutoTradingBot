import json

import httpx

from broker.base import BrokerPort
from broker.kisAuth import KisAuth
from broker.kisParsers import KisParsers
from broker.kisSpecs import KisSpecs
from config.settings import settings
from core.models import OrderBookSnapshot, OrderRequest, OrderResult, Position


class KisRestBroker(BrokerPort):
    def __init__(self, auth: KisAuth) -> None:
        self.auth = auth
        self.client = httpx.AsyncClient(timeout=10.0)

    async def _authorizedHeaders(self, trId: str, includeHashkey: bool = False, body: dict | None = None) -> dict:
        accessToken = await self.auth.getAccessToken()
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {accessToken}",
            "appkey": settings.kisAppKey,
            "appsecret": settings.kisAppSecret,
            "tr_id": trId,
            "custtype": settings.kisCusttype,
        }

        if includeHashkey and body is not None:
            hashkey = await self._hashkey(body)
            headers["hashkey"] = hashkey

        return headers

    async def _hashkey(self, body: dict) -> str:
        url = f"{settings.kisRestBaseUrl}{KisSpecs.hashkeyPath}"
        headers = {
            "content-type": "application/json",
            "appkey": settings.kisAppKey,
            "appsecret": settings.kisAppSecret,
        }
        response = await self.client.post(url, headers=headers, content=json.dumps(body))
        response.raise_for_status()
        data = response.json()
        return data["HASH"]

    async def getOrderBook(self, symbol: str) -> OrderBookSnapshot:
        url = f"{settings.kisRestBaseUrl}{KisSpecs.inquireOrderbookPath}"
        headers = await self._authorizedHeaders(KisSpecs.trOrderbook)
        params = KisSpecs.buildOrderbookParams(symbol)

        response = await self.client.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        return KisParsers.parseOrderbookResponse(symbol, data)

    async def placeOrder(self, orderRequest: OrderRequest) -> OrderResult:
        url = f"{settings.kisRestBaseUrl}{KisSpecs.orderCashPath}"

        if orderRequest.side == "buy":
            body = KisSpecs.buildBuyBody(orderRequest.symbol, orderRequest.quantity, orderRequest.price)
            trId = KisSpecs.trBuy
        elif orderRequest.side == "sell":
            body = KisSpecs.buildSellBody(orderRequest.symbol, orderRequest.quantity, orderRequest.price)
            trId = KisSpecs.trSell
        else:
            raise ValueError(f"지원하지 않는 주문 방향: {orderRequest.side}")

        headers = await self._authorizedHeaders(trId, includeHashkey=True, body=body)
        response = await self.client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

        return OrderResult(
            success=data.get("rt_cd") == "0",
            orderId=data.get("output", {}).get("odno", ""),
            message=data.get("msg1", ""),
            raw=data,
        )

    async def getPositions(self) -> list[Position]:
        # TODO: 잔고조회 path / tr_id / 응답 파싱 추가
        return []

    async def close(self) -> None:
        await self.client.aclose()