import asyncio
import json
from collections.abc import AsyncIterator

import websockets

from broker.kisAuth import KisAuth
from broker.kisSpecs import KisSpecs
from config.settings import settings


class KisWsClient:
    def __init__(self, auth: KisAuth) -> None:
        self.auth = auth
        self.websocket = None

    async def connect(self) -> None:
        if not settings.kisWsBaseUrl:
            raise RuntimeError("KIS_WS_BASE_URL 환경변수가 비어 있습니다.")

        self.websocket = await websockets.connect(
            settings.kisWsBaseUrl,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        )

    async def subscribeOrderbook(self, symbol: str) -> None:
        if self.websocket is None:
            raise RuntimeError("WebSocket 연결이 안 되어 있습니다.")

        approvalKey = await self.auth.getWsApprovalKey()
        message = KisSpecs.buildWsSubscribeMessage(
            trId=KisSpecs.wsTrOrderbook,
            trKey=symbol,
            approvalKey=approvalKey,
        )
        await self.websocket.send(json.dumps(message, ensure_ascii=False))

    async def listen(self) -> AsyncIterator[str]:
        if self.websocket is None:
            raise RuntimeError("WebSocket 연결이 안 되어 있습니다.")
        async for message in self.websocket:
            yield message

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()