import api.hoga as hoga



codes = ["005930", "000660", "035420"]  # 예시 종목 코드 리스트

hoga_data = asyncio.run(hoga.fetch_all_hoga(codes))

print(hoga_data)