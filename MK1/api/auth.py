
import requests
import json

import api.token as token


def get_appkey():
    return token.APP_KEY
def get_appsecret():
    return token.APP_SECRET

def get_access_token_from_api():
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"

    headers = {
        "Content-Type": "application/json"
    }

    body = {
        "grant_type": "client_credentials",
        "appkey": token.APP_KEY,
        "appsecret": token.APP_SECRET
    }

    try:
        res = requests.post(url, headers=headers, data=json.dumps(body))
        data = res.json()

        if res.status_code == 200 and "access_token" in data:
            return data["access_token"]
        else:
            print("토큰 발급 실패:", data)
            return None

    except Exception as e:
        print("에러 발생:", e)
        return None


token = get_access_token_from_api()
def get_access_token():
    global token
    if token is None:
        token = get_access_token_from_api()
    return token