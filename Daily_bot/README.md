# Daily Bot

문서보다 코드를 우선 진실원천으로 본다. 현재 실제 동작 기준 파일은 [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml), [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py), [replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)다.

## 문서 안내

- 현재 운영값 요약: [CURRENT_DAILY_SETTINGS.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CURRENT_DAILY_SETTINGS.md)
- 현재 전략 개념과 재구성 설명: [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)
- 빠른 인수인계 메모: [CODEX_HANDOFF.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CODEX_HANDOFF.md)
- 제로베이스 재구축용 개념 문서: [curr_strategy.txt](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/curr_strategy.txt)

## 현재 전략 한 줄 요약

데일리 봇은 당일 KOSPI 전체에서 유동성 필터를 통과한 종목만 대상으로 60초마다 호가를 다시 스캔하고, 양쪽 호가잔량에 강한 대칭 선형 감쇠를 적용한 뒤 계산한 기대수익률이 0.7% 이상인 후보만 자본 기반 슬롯 구조 안에서 즉시 매수하고 즉시 목표가 매도로 정리하는 장중 전략이다.

## 현재 운영값 요약

- 시장: `KOSPI`
- 유니버스: 당일 조회한 KOSPI 전체 종목 중 시가총액 `2500억` 이상, 거래대금 `30억` 이상
- 스캔 주기: `60초`
- 기대수익률 기준: `0.7`
- fallback: `비활성화`
- 호가 모델: `매수/매도 모두 1.0 -> 0.1 강한 대칭 선형 감쇠`
- 신규 진입 시간: `09:30 ~ 11:30`
- 장 시작 전 이월 포지션 정리: `09:10`
- 강제청산: `15:00`
- 스캔당 신규 매수 상한: `3종목`
- 총 보유 종목 하드 상한: `10종목`
- 빈 슬롯 재매수: `허용`
- 전일 상승 상한 필터: `1.0%`
- 장중 손절: `비활성화`
- 일손실 제한: `10%`

## 중요한 해석 포인트

- `max_buy_count = 3`은 총 보유 수 제한이 아니라 한 번의 스캔에서 추가로 새로 살 수 있는 종목 수 상한이다.
- 실제 총 보유 수는 자본 규모에서 계산된 슬롯 수와 `risk.max_position_count = 10`의 조합으로 결정된다.
- fallback은 현재 설정에서 꺼져 있다. 즉 현재 운영은 `0.7 단일`이다.
- 재매수는 허용되어 있다. 다만 한 번의 스캔에서 새로 진입하는 수는 최대 3개로 제한된다.
- 손절 후 당일 재진입 차단 코드는 남아 있지만, 현재 손절 자체가 꺼져 있으므로 실제 운영 중에는 거의 작동하지 않는다.

## 백테스트 정합성 요약

- 리플레이는 `market_traces.raw_json`에서 호가를 다시 읽어 같은 기대수익률 계산 구조를 재현한다.
- 현재 백테스트 기본값도 실코드와 동일하게 `강한 대칭 감쇠 + 무손절`을 사용한다.
- `scan_cycle_at` 배치 기준, `scan_candidate` 기준 진입, 목표가 체결가 고정, 자본 기반 조합 선택을 맞춘 상태다.
- 여전히 60초 스캔 사이의 순간 고가/저가, 부분체결, 취소 후 재주문 세부 흐름까지 완전히 복원하는 것은 아니다.

## 실행 예시

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\main.py --real
```

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --logs-dir .\Daily_bot\logs
```
