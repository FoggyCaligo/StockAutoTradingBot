# Daily Bot

문서보다 코드를 우선 기준으로 본다. 현재 실제 동작 기준 파일은 [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml), [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py), [replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py) 다.

## 문서 안내

- 현재 운영값 요약: [CURRENT_DAILY_SETTINGS.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CURRENT_DAILY_SETTINGS.md)
- 전략/동작 개념 문서: [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)
- 인수인계 메모: [CODEX_HANDOFF.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CODEX_HANDOFF.md)

## 현재 전략 한 줄 요약

데일리 봇은 당일 KOSPI 전체에서 유동성 있는 대형주만 추려 60초 단위로 20호가를 스캔하고, 기대수익률 기준을 통과한 후보만 자본 기반 슬롯 계획으로 짧게 매수하고 짧게 청산하는 intraday 배치형 전략이다.

## 현재 운영값 핵심

- 유니버스: `KOSPI`
- 기본 기대수익률 문턱: `0.7`
- fallback: `[0.6, 0.5]`
- 스캔 주기: `60초`
- 신규 진입 배치 상한: `max_buy_count = 3`
- 총 보유 종목 상한: `risk.max_position_count = 10`
- 손절: `-4.5%`
- 매수 가능 시간: `09:30 ~ 11:30`
- 강제청산: `15:00`

## 중요 오해 방지

- `max_buy_count = 3` 은 총 보유 종목 수가 아니라 “한 번의 신규 진입 배치 상한”이다.
- 현재 기본 실거래는 빈 슬롯이 생겨도 전체 배치가 완전히 비기 전까지 재진입하지 않는다.
- fallback 은 언제나 쓰는 것이 아니라, 배치가 완전히 비어 있고 기본 문턱에서 후보가 0개일 때만 작동한다.

## 백테스트 요약

리플레이 엔트리포인트는 [replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py) 다.

현재 리플레이는 다음을 라이브 쪽에 맞춰 두었다.

- `scan_cycle_at` 기준 스캔 배치 재구성
- 신규 진입 시 `scan_candidate` 배치만 사용
- fallback 규칙 일치
- 직전 스캔 급등 필터 반영
- `select_affordable_targets` 조합 선택 반영
- 목표가 초과 시 익절 체결가는 `target_price` 로 고정

다만 부분체결, 주문 취소-재주문, 60초 사이 순간 고가/저가까지 완전히 재현하지는 못한다.

## 예시 명령

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\main.py --real
```

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --logs-dir .\Daily_bot\logs
```

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --logs-dir .\Daily_bot\logs --use-selected-signals
```
