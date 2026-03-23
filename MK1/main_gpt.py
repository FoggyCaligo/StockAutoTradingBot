import aiohttp
import asyncio

BASE_URL = "https://openapi.koreainvestment.com:9443"

APP_KEY = "YOUR_APP_KEY"
APP_SECRET = "YOUR_APP_SECRET"
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

HEADERS = {
    "Content-Type": "application/json",
    "authorization": f"Bearer {ACCESS_TOKEN}",
    "appKey": APP_KEY,
    "appSecret": APP_SECRET,
    "tr_id": "FHKST01010200"
}

# ---------------------------
# Rate Limit 제어
# ---------------------------
semaphore = asyncio.Semaphore(10)


# ---------------------------
# 호가 데이터 가져오기
# ---------------------------
async def get_hoga_data(session, code):
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code
    }

    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, params=params) as resp:
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


# ---------------------------
# 균형가격 계산 (핵심 알고리즘)
# ---------------------------
def calc_equilibrium(orderbook):
    ask_p = orderbook["ask_price"]
    ask_q = orderbook["ask_size"]
    bid_p = orderbook["bid_price"]
    bid_q = orderbook["bid_size"]

    i, j = 0, 0

    ask_rem = ask_q[0]
    bid_rem = bid_q[0]

    last_price = None

    # 양쪽 소진될 때까지 매칭
    while i < 10 and j < 10:
        trade_qty = min(ask_rem, bid_rem)

        # 마지막 체결 가격 기록 (중간값 계산용)
        last_price = (ask_p[i] + bid_p[j]) / 2

        ask_rem -= trade_qty
        bid_rem -= trade_qty

        if ask_rem == 0:
            i += 1
            if i < 10:
                ask_rem = ask_q[i]

        if bid_rem == 0:
            j += 1
            if j < 10:
                bid_rem = bid_q[j]

    # fallback: 매칭이 거의 없을 경우
    if last_price is None:
        return (ask_p[0] + bid_p[0]) / 2

    return last_price


# ---------------------------
# 병렬 호출
# ---------------------------
async def fetch_all_hoga(codes):
    async with aiohttp.ClientSession() as session:
        tasks = [get_hoga_data(session, code) for code in codes]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]


# ---------------------------
# 배치 처리
# ---------------------------
async def fetch_in_batches(codes, batch_size=10):
    results = []

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        res = await fetch_all_hoga(batch)
        results.extend(res)

        await asyncio.sleep(1)  # rate limit 보호

    return results


# ---------------------------
# 실행 (전략 적용)
# ---------------------------
def run_strategy(codes):
    results = asyncio.run(fetch_in_batches(codes))

    candidates = []

    for ob in results:
        eq_price = calc_equilibrium(ob)

        best_bid = ob["bid_price"][0]
        expected_return = (eq_price - best_bid) / best_bid

        candidates.append({
            "code": ob["code"],
            "eq_price": eq_price,
            "bid": best_bid,
            "expected_return": expected_return
        })

    # 수익률 기준 정렬
    candidates.sort(key=lambda x: x["expected_return"], reverse=True)

    return candidates[:5]


# ---------------------------
# 사용 예시
# ---------------------------
if __name__ == "__main__":
    codes = ["005930", "000660", "035420"]  # 예시

    top5 = run_strategy(codes)

    for c in top5:
        print(c)