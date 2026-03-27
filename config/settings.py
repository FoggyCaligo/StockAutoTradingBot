from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    appEnv: str
    kisAppKey: str
    kisAppSecret: str
    kisHtsId: str
    kisAccountNo: str
    kisAccountProductCode: str

    kisRestBaseUrl: str
    kisWsBaseUrl: str

    kisTokenPath: str
    kisWsApprovalPath: str
    kisInquirePricePath: str
    kisInquireOrderbookPath: str
    kisOrderCashPath: str
    kisHashkeyPath: str

    kisTrPrice: str
    kisTrOrderbook: str
    kisTrBuy: str
    kisTrSell: str
    kisTrBalance: str

    kisWsTrOrderbook: str
    kisWsTrTrade: str
    kisCusttype: str

    topK: int
    entryThreshold: float
    maxSpreadRatio: float
    stopLossRatio: float
    takeProfitRatio: float
    maxHoldSeconds: int
    maxDailyLoss: int

    @staticmethod
    def fromEnv() -> "Settings":
        return Settings(
            appEnv=os.getenv("APP_ENV", "paper"),
            kisAppKey=os.getenv("KIS_APP_KEY", ""),
            kisAppSecret=os.getenv("KIS_APP_SECRET", ""),
            kisHtsId=os.getenv("KIS_HTS_ID", ""),
            kisAccountNo=os.getenv("KIS_ACCOUNT_NO", ""),
            kisAccountProductCode=os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01"),
            kisRestBaseUrl=os.getenv("KIS_REST_BASE_URL", "https://openapi.koreainvestment.com:9443"),
            kisWsBaseUrl=os.getenv("KIS_WS_BASE_URL", ""),
            kisTokenPath=os.getenv("KIS_TOKEN_PATH", "/oauth2/tokenP"),
            kisWsApprovalPath=os.getenv("KIS_WS_APPROVAL_PATH", ""),
            kisInquirePricePath=os.getenv("KIS_INQUIRE_PRICE_PATH", ""),
            kisInquireOrderbookPath=os.getenv("KIS_INQUIRE_ORDERBOOK_PATH", ""),
            kisOrderCashPath=os.getenv("KIS_ORDER_CASH_PATH", ""),
            kisHashkeyPath=os.getenv("KIS_HASHKEY_PATH", ""),
            kisTrPrice=os.getenv("KIS_TR_PRICE", ""),
            kisTrOrderbook=os.getenv("KIS_TR_ORDERBOOK", ""),
            kisTrBuy=os.getenv("KIS_TR_BUY", ""),
            kisTrSell=os.getenv("KIS_TR_SELL", ""),
            kisTrBalance=os.getenv("KIS_TR_BALANCE", ""),
            kisWsTrOrderbook=os.getenv("KIS_WS_TR_ORDERBOOK", ""),
            kisWsTrTrade=os.getenv("KIS_WS_TR_TRADE", ""),
            kisCusttype=os.getenv("KIS_CUSTTYPE", "P"),
            topK=int(os.getenv("TOP_K", "3")),
            entryThreshold=float(os.getenv("ENTRY_THRESHOLD", "0.008")),
            maxSpreadRatio=float(os.getenv("MAX_SPREAD_RATIO", "0.0015")),
            stopLossRatio=float(os.getenv("STOP_LOSS_RATIO", "0.005")),
            takeProfitRatio=float(os.getenv("TAKE_PROFIT_RATIO", "0.010")),
            maxHoldSeconds=int(os.getenv("MAX_HOLD_SECONDS", "300")),
            maxDailyLoss=int(os.getenv("MAX_DAILY_LOSS", "150000")),
        )


settings = Settings.fromEnv()