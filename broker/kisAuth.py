import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from config.settings import settings
from broker.kisSpecs import KisSpecs


@dataclass
class TokenInfo:
    accessToken: str
    expiresAt: datetime


class KisAuth:
    def __init__(self) -> None:
        self.cachePath = Path(".cache")
        self.cachePath.mkdir(exist_ok=True)
        self.tokenFile = self.cachePath / "kisToken.json"
        self.wsApprovalFile = self.cachePath / "kisWsApproval.json"

    async def getAccessToken(self, forceRefresh: bool = False) -> str:
        if not forceRefresh:
            cached = self._loadToken()
            if cached and cached.expiresAt > datetime.now(timezone.utc) + timedelta(minutes=10):
                return cached.accessToken

        tokenInfo = await self._issueAccessToken()
        self._saveToken(tokenInfo)
        return tokenInfo.accessToken

    async def getWsApprovalKey(self, forceRefresh: bool = False) -> str:
        # 웹소켓 승인키는 토큰과 별도 관리
        if not forceRefresh and self.wsApprovalFile.exists():
            data = json.loads(self.wsApprovalFile.read_text(encoding="utf-8"))
            if data.get("approvalKey"):
                return data["approvalKey"]

        approvalKey = await self._issueWsApprovalKey()
        self.wsApprovalFile.write_text(
            json.dumps({"approvalKey": approvalKey}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return approvalKey

    async def _issueAccessToken(self) -> TokenInfo:
        url = f"{settings.kisRestBaseUrl}{KisSpecs.tokenPath}"
        body = {
            "grant_type": "client_credentials",  # 필요시 환경별로 조정
            "appkey": settings.kisAppKey,
            "appsecret": settings.kisAppSecret,
        }
        headers = {"content-type": "application/json"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()

        accessToken = data["access_token"]
        expiresIn = int(data.get("expires_in", 7776000))
        expiresAt = datetime.now(timezone.utc) + timedelta(seconds=expiresIn)
        return TokenInfo(accessToken=accessToken, expiresAt=expiresAt)

    async def _issueWsApprovalKey(self) -> str:
        if not KisSpecs.wsApprovalPath:
            raise RuntimeError("KIS_WS_APPROVAL_PATH 환경변수가 비어 있습니다.")

        url = f"{settings.kisRestBaseUrl}{KisSpecs.wsApprovalPath}"
        body = {
            "grant_type": "client_credentials",
            "appkey": settings.kisAppKey,
            "secretkey": settings.kisAppSecret,
        }
        headers = {"content-type": "application/json"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()

        approvalKey = data.get("approval_key") or data.get("approvalKey")
        if not approvalKey:
            raise RuntimeError(f"웹소켓 승인키 응답 해석 실패: {data}")
        return approvalKey

    def _loadToken(self) -> TokenInfo | None:
        if not self.tokenFile.exists():
            return None
        data = json.loads(self.tokenFile.read_text(encoding="utf-8"))
        return TokenInfo(
            accessToken=data["accessToken"],
            expiresAt=datetime.fromisoformat(data["expiresAt"]),
        )

    def _saveToken(self, tokenInfo: TokenInfo) -> None:
        self.tokenFile.write_text(
            json.dumps(
                {
                    "accessToken": tokenInfo.accessToken,
                    "expiresAt": tokenInfo.expiresAt.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )