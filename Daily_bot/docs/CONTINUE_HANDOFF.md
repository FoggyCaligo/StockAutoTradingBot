# Daily Bot - CONTINUE HANDOFF

## 현재 상태

- 실코드와 백테스트 기본 설정은 현재 동일한 방향으로 맞춰져 있다.
- 핵심 설정은 `0.7 단일`, `상위 25% 컷`, `재매수 허용`, `전일 1.0% 상한`, `장중 손절 OFF`, `양쪽 호가 1.0 -> 0.1 대칭 감쇠`다.
- 호가 기대수익 계산은 실코드와 백테스트가 공용 감쇠 함수를 사용한다.

## 지금 봐야 할 파일

- 런타임: [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py)
- 설정: [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)
- 호가 계산: [orderbook_predictor.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/strategy/orderbook_predictor.py)
- 기대수익 계산: [signal.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/strategy/signal.py)
- 백테스트: [replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)
- 전략 개념 문서: [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)

## 이어서 작업할 때 주의할 점

- `max_buy_count = 3`은 총 보유 상한이 아니라 스캔당 신규 진입 상한이다.
- 현재 손절은 꺼져 있으므로 손절 후 재진입 차단 로직은 사실상 유휴 상태다.
- 백테스트는 `raw_json` 호가 재구성과 `scan_cycle_at` 배치 재구성을 쓰므로, 기대수익 계산식을 바꾸면 실코드와 백테스트를 같이 봐야 한다.
- 실험 결과를 해석할 때는 총손익뿐 아니라 거래 수, 강제청산 비중, 나쁜 날 하방 변화를 같이 봐야 한다.
