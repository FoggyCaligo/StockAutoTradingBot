import aiohttp
import asyncio
import api.auth as auth

BASE_URL = "https://openapi.koreainvestment.com:9443"

headers = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {auth.get_access_token()}",
    "appKey": auth.get_appkey(),
    "appSecret": auth.get_appsecret(),
    "tr_id": ""
}

async def get_hoga_data(session, code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    headers["tr_id"] = "FHKST01010100"  # TR ID 설정 - 매도호가/매수호가 조회 TR ID
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code
    }

    try:
        async with session.get(url, headers=headers, params=params) as resp:
            data = await resp.json()

            if data.get("rt_cd") != "0":
                return None

            ob = data["output1"]

            return {
                "code": code,
                "ask_price": [int(ob[f"askp{i}"]) for i in range(1, 11)],
                "ask_size": [int(ob[f"askp_rsqn{i}"]) for i in range(1, 11)],
                "bid_price": [int(ob[f"bidp{i}"]) for i in range(1, 11)],
                "bid_size": [int(ob[f"bidp_rsqn{i}"]) for i in range(1, 11)],
            }
    except Exception:
        return None


async def fetch_all_hoga(codes):
    async with aiohttp.ClientSession() as session:
        tasks = [get_hoga_data(session, code) for code in codes]
        results = await asyncio.gather(*tasks)

        # None 제거
        return [r for r in results if r is not None]

print(asyncio.run(fetch_all_hoga(["005930"])))