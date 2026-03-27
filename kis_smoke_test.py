import os
import requests
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()

# 실전이면 보통 이 URL
BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")

def issue_token() -> str:
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json; charset=utf-8"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }

    resp = requests.post(url, headers=headers, json=body, timeout=10)
    print("TOKEN STATUS:", resp.status_code)
    print("TOKEN BODY:", resp.text[:500])
    resp.raise_for_status()

    data = resp.json()
    return data["access_token"]

def get_orderbook(access_token: str, symbol: str = "005930") -> None:
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010200",
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
    }

    resp = requests.get(url, headers=headers, params=params, timeout=10)
    print("ORDERBOOK STATUS:", resp.status_code)
    print("ORDERBOOK BODY:", resp.text[:1000])

if __name__ == "__main__":
    token = issue_token()
    print("ACCESS TOKEN PREFIX:", token[:20], "...")
    get_orderbook(token, "005930")