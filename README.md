# StockAutoTradingBot
stock auto trading bot in kospi
키움증권 대신 한국투자증권 api 사용하도록 재구성하는 프로젝트



1. Set-ExecutionPolicy RemoteSigned -scope CurrentUser
2. powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
3. uv sync

broker는 한국투자 API만 안다
strategy는 호가 배열만 안다
storage는 SQLite만 안다
runner가 이 셋을 연결한다
