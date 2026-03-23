
import requests
import json

import api.auth as auth

def get_headers(tr_id, hashkey):
    return {
        "Content-Type": "application/json",
        "authorization": f"Bearer {auth.get_access_token()}",
        "appKey": auth.get_appkey(),
        "appSecret": auth.get_appsecret(),
        "tr_id": tr_id,
        "custtype": "P",
        "hashkey": hashkey
    }

def get_hashkey(data):
    url = "https://openapi.koreainvestment.com:9443/uapi/hashkey"

    headers = {
        "Content-Type": "application/json",
        "appKey": auth.get_appkey(),
        "appSecret": auth.get_appsecret()
    }

    res = requests.post(url, headers=headers, data=json.dumps(data))
    return res.json()["HASH"]

def buy_stock(code, qty, price, account, product_code="01"): #주문은 전부 문자열로 해야 함.
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash"

    data = {
        "CANO": account[:8],          # 계좌 앞 8자리
        "ACNT_PRDT_CD": product_code, # 계좌 뒤 2자리
        "PDNO": code,                 # 종목코드
        "ORD_DVSN": "00",             # 00: 지정가, 01: 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": str(price)        # 시장가는 "0"
    }

    hashkey = get_hashkey(data)
    headers = get_headers("TTTC0012U", auth.get_access_token(), auth.get_appkey(), auth.get_appsecret(), hashkey)

    res = requests.post(url, headers=headers, data=json.dumps(data))
    return res.json()

def sell_stock(code, qty, price, account, product_code="01"):
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash"

    data = {
        "CANO": account[:8],
        "ACNT_PRDT_CD": product_code,
        "PDNO": code,
        "ORD_DVSN": "00",   # 지정가
        "ORD_QTY": str(qty),
        "ORD_UNPR": str(price)
    }

    hashkey = get_hashkey(data)
    headers = get_headers("TTTC0011U", auth.get_access_token(), auth.get_appkey(), auth.get_appsecret(), hashkey)

    res = requests.post(url, headers=headers, data=json.dumps(data))
    return res.json()