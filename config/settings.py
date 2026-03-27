import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    kisAppKey: str
    kisAppSecret: str
    kisAccessToken: str
    kisAccountNo: str
    kisProductCode: str
    kisBaseUrl: str

    budget: int
    topK: int
    minExpectedReturn: float
    scanIntervalSeconds: int
    symbols: list[str]

    @staticmethod
    def fromEnv() -> "Settings":
        rawSymbols = os.getenv(
            "SYMBOLS",
            "005930,000660,035420,005380,035720"
        )

        return Settings(
            kisAppKey=os.getenv("KIS_APP_KEY", "").strip(),
            kisAppSecret=os.getenv("KIS_APP_SECRET", "").strip(),
            kisAccessToken=os.getenv("KIS_ACCESS_TOKEN", "").strip(),
            kisAccountNo=os.getenv("KIS_ACCOUNT_NO", "").strip(),
            kisProductCode=os.getenv("KIS_PRODUCT_CODE", "01").strip(),
            kisBaseUrl=os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").strip(),
            budget=int(os.getenv("BUDGET", "500000")),
            topK=int(os.getenv("TOP_K", "4")),
            minExpectedReturn=float(os.getenv("MIN_EXPECTED_RETURN", "0.00315")),
            scanIntervalSeconds=int(os.getenv("SCAN_INTERVAL_SECONDS", "3")),
            symbols=[s.strip() for s in rawSymbols.split(",") if s.strip()],
        )


settings = Settings.fromEnv()