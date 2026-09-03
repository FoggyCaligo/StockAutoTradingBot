# Daily Bot

문서보다 코드를 우선 진실원천으로 본다. 현재 실제 동작 기준 파일은 [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml), [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py), [replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)다.

## 현재 전략 한 줄 요약

데일리 봇은 당일 KOSPI&KOSDAQ 전체에서 유동성 필터를 통과한 종목만 대상으로 60초마다 호가를 다시 스캔하고, 양쪽 호가잔량에 강한 대칭 선형 감쇠를 적용한 뒤 계산한 기대수익률이 0.7% 이상인 후보만 자본 기반 슬롯 구조 안에서 즉시 매수하고 즉시 목표가 매도로 정리하는 장중 전략이다.

## 현재 운영값 요약

- 시장: `KOSPI & KOSDAQ`
- 유니버스: 당일 조회한 KOSPI&KOSDAQ 전체 종목 중 시가총액 `2500억` 이상, 거래대금 `30억` 이상
- 스캔 주기: `60초`
- 기대수익률 기준: `0.71`
- 랭킹 컷: `상위 25%`
- fallback: `비활성화`
- 호가 모델: `매수/매도 모두 1.0 -> 0.0 더 강한 대칭 선형 감쇠`
- 신규 진입 시간: `09:30 ~ 11:30`
- 장 시작 전 이월 포지션 정리: `09:10`
- 강제청산: `15:00`
- 스캔당 신규 매수 상한: `3종목`
- 총 보유 종목 하드 상한: `10종목`
- 빈 슬롯 재매수: `허용`
- 전일 상승 상한 필터: `10.0%`
- 고정 장중 손절: `비활성화`
- 동적 예상수익률 손절: `.env`의 `DYNAMIC_EXPECT_STOP_PERCENT` 사용, 기본 `-0.3%`
- 일손실 제한: `10%`

## 중요한 해석 포인트

- `max_buy_count = 3`은 총 보유 수 제한이 아니라 한 번의 스캔에서 추가로 새로 살 수 있는 종목 수 상한이다.
- 실제 총 보유 수는 자본 규모에서 계산된 슬롯 수와 `risk.max_position_count = 10`의 조합으로 결정된다.
- fallback은 현재 설정에서 꺼져 있다. 즉 현재 운영은 `0.7 단일`이다.
- 현재는 기대수익률 계산이 끝난 후보 중 상위 25%만 다음 단계로 넘긴다.
- 재매수는 허용되어 있다. 다만 한 번의 스캔에서 새로 진입하는 수는 최대 3개로 제한된다.
- 고정 손절은 꺼져 있지만, 동적 예상수익률 손절은 별도로 작동한다. 기본값은 현재가 대비 예상수익률 `-0.3%` 이하이며 `.env`에서 조정하거나 `off`로 끌 수 있다.

## 백테스트 정합성 요약

- 리플레이는 `market_traces.raw_json`에서 호가를 다시 읽어 같은 기대수익률 계산 구조를 재현한다.
- 백테스트 기준 DB는 `Daily_bot/backtest/cache/rebuild_from_logs_replay_from_logs.sqlite3`다.
- 이 DB는 `Daily_bot/logs/market_traces_*.csv`에서 복원한 전용 replay DB이며, 실거래 DB `Daily_bot/bot.sqlite3`와 분리한다.
- 현재 백테스트 기본값도 실코드와 동일하게 `강한 대칭 감쇠 + 고정 손절 비활성화`를 사용한다.
- `scan_cycle_at` 배치 기준, `scan_candidate` 기준 진입, 목표가 체결가 고정, 자본 기반 조합 선택을 맞춘 상태다.
- 여전히 60초 스캔 사이의 순간 고가/저가, 부분체결, 취소 후 재주문 세부 흐름까지 완전히 복원하는 것은 아니다.
- `Daily_bot/backtest/results/*.csv`는 실행 결과물이다. 진단 전에 replay를 먼저 실행해 `backtest_replay.csv`를 생성해야 한다.

## 실행 예시

실거래:

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\main.py --real
```

Git Bash에서 Git에 기록된 market trace 로그로 replay DB와 기본 백테스트 결과를 다시 생성:

```bash
python Daily_bot/backtest/replay_market_traces.py \
  --db Daily_bot/rebuild_from_logs.sqlite3 \
  --logs-dir Daily_bot/logs
```

위 명령의 replay DB 결과:

```text
Daily_bot/backtest/cache/rebuild_from_logs_replay_from_logs.sqlite3
```

기본 거래 결과:

```text
Daily_bot/backtest/results/backtest_replay.csv
Daily_bot/backtest/results/backtest_replay_daily_rev.csv
Daily_bot/backtest/results/backtest_replay_trade_fills_audit_daily.csv
```

복원된 replay DB를 사용한 일반 백테스트:

```bash
python Daily_bot/backtest/replay_market_traces.py \
  --db Daily_bot/backtest/cache/rebuild_from_logs_replay_from_logs.sqlite3
```

동적 예상수익률 진단:

```bash
python Daily_bot/backtest/diagnose_dynamic_expect_stop.py \
  --db Daily_bot/backtest/cache/rebuild_from_logs_replay_from_logs.sqlite3 \
  --trades Daily_bot/backtest/results/backtest_replay.csv
```

진단 결과:

```text
Daily_bot/backtest/results/dynamic_expect_stop_diagnostics.csv
Daily_bot/backtest/results/dynamic_expect_stop_threshold_sweep.csv
```
