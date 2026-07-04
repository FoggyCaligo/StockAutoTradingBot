# Daily Bot

Daily Bot은 `KOSPI200` 유니버스를 장중 스캔해, 기대수익률 기준을 통과한 종목만 제한적으로 진입하는 당일 매매 봇이다.

문서보다 코드를 우선 기준으로 본다. 현재 실제 동작 기준 파일은 [config/settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml), [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py), [backtest/replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)다.

## 현재 운영 시간

```yaml
market:
  prewarm_start_time: "08:55"
  start_buy_time: "09:30"
  stop_buy_time: "11:30"
  force_sell_time: "15:00"
  reconcile_time: "15:15"
  end_time: "15:20"
```

- `08:55 ~ 09:30`: 유니버스 프리웜, 전일종가 기록, 세션 슬롯 계획 확정
- `09:30 ~ 11:30`: 신규 매수 가능
- `11:30 ~ 15:00`: 신규 매수 중단, 보유 포지션과 주문만 관리
- `15:00`: 강제 청산 시작
- `15:15`: 브로커 체결 대조 및 기록 보정
- `15:20`: 세션 종료

## 현재 실거래 설정

```yaml
universe:
  source: "KOSPI200"
  min_market_cap_krw: 250000000000
  min_trading_value_krw: 3000000000

trend_filter:
  enabled: false

strategy:
  top_ratio: 1.0
  max_buy_count: 3
  min_expected_return_percent: 0.7
  min_expected_return_fallback_percent: 0.4
  max_spread_percent: 0.0
  spread_expected_return_multiplier: 0.0
  min_prev_day_change_percent: 0.0
  max_prev_day_change_percent: 0.0
  max_intraday_jump_from_prev_scan_percent: 0.0
  sell_tick_offset: 1
  scan_interval_seconds: 60

risk:
  dry_run: false
  max_position_count: 10
  min_slot_count: 3
  slot_budget_unit_krw: 5000000
  max_slot_count: 10
  target_budget_ratio_per_stock: 0.50
  max_budget_per_cycle_krw: 0
  max_budget_per_stock_krw: 0
  max_orderbook_ask_depth_ratio: 0.0
  stop_loss_tick_count: 0
  stop_loss_tick_multiplier: 0.0
  stop_loss_percent: 4.5
  daily_loss_limit_percent: 10.0
```

## 현재 실거래 흐름

1. 세션 시작 전에 유니버스를 준비하고 전일종가를 기록한다.
2. 세션 자본 기준으로 슬롯 수와 종목당 예산을 계산한다.
3. `KOSPI200` 전 종목을 스캔하며 20호가 기준 기대가와 기대수익률을 계산한다.
4. 아래 조건을 통과한 종목만 최종 후보로 남긴다.

- 기본은 `expect_revenue_percent >= 0.7`
- 단, 보유/미체결이 전혀 없는 신규 배치 시작 시점에 `0.7` 기준 후보가 0개면 같은 스캔 결과를 `0.4` 기준으로 한 번 더 재평가한다.
- `prev_day_change_percent < 0.0`이 아니라, 현재 설정상 `min_prev_day_change_percent = 0.0`, `max_prev_day_change_percent = 0.0`이므로 전일등락률 필터는 사실상 꺼져 있다.
- `max_spread_percent = 0.0`이므로 스프레드 필터도 꺼져 있다.
- 목표 매도가가 진입가보다 낮으면 제외한다.

5. 이미 보유 중이거나 미체결 주문이 있는 종목은 다시 사지 않는다.
6. 현재 로직은 `빈 슬롯이 일부 생겼다고 바로 재진입하지 않고`, 활성 포지션이나 오더가 하나라도 남아 있으면 다음 배치를 기다린다.
7. 매수 체결 직후 목표가 지정가 매도를 제출한다.
8. 손절 조건이 오면 기존 오더를 취소하고 손절 매도를 낸다.
9. `15:00` 이후에는 남은 포지션을 강제 청산한다.
10. `15:15`에 브로커 체결내역으로 `fills`를 다시 맞춘다.

## 자본 배분

- 세션 자본 기준으로 슬롯 수를 계산한다.
- 기본 슬롯 단위는 `5,000,000 KRW`다.
- 최소 슬롯 수는 `3`, 최대 슬롯 수는 `10`이다.
- 한 번의 배치에서 신규 진입 종목 수는 `max_buy_count = 3` 이하다.
- `max_position_count = 10`은 하드 상한이지만, 실제 신규 진입은 배치 대기 로직 때문에 더 보수적으로 동작한다.

## 손절 및 종료

- 현재 손절은 `stop_loss_percent = 4.5`만 활성화돼 있다.
- `stop_loss_tick_count = 0`, `stop_loss_tick_multiplier = 0.0`이라 tick 기반 손절은 꺼져 있다.
- 일손실 한도 `10%`에 도달하면 신규 매수만 막고, 기존 포지션 정리 로직은 계속 돈다.

## 백테스트 기본 동작

리플레이 백테스트 엔트리포인트는 [backtest/replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)다.

- `--config`를 주면 그 파일에서 기본값을 읽는다.
- `--config`를 생략하면 `Daily_bot/config/settings.yaml`을 읽는다.
- 기본 `min_expected_return`, 시간 설정, 손절값, 슬롯 설정은 현재 설정 파일과 정렬된다.
- `--starting-capital-krw` 기본값은 `1,000,000 KRW`다.
- `--use-selected-signals` 기본값은 `False`다.
- `--fallback-min-expected-return` 기본값은 config의 `strategy.min_expected_return_fallback_percent`다.
- 백테스트도 실거래와 같은 규칙으로, 배치가 완전히 비어 있고 기본 기대수익률 문턱에서 후보가 0개일 때만 fallback 문턱으로 다시 고른다.
- 즉, 아무 옵션 없이 돌리면 백테스트는 실제로 저장된 `selected signals`를 강제 재현하지 않고, 저장된 `market_traces`에서 조건을 만족하는 후보를 다시 고른다.

실거래 비교 목적이면 보통 `--use-selected-signals` 여부를 명시해서 돌리는 편이 안전하다.

예시:

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --db Daily_bot\bot.sqlite3
```

실거래 선택 신호를 따라가고 싶다면:

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --db Daily_bot\bot.sqlite3 --use-selected-signals
```

## 로그와 산출물

실거래 로그:

- `Daily_bot/logs/orders_YYYYMMDD.csv`
- `Daily_bot/logs/fills_YYYYMMDD.csv`
- `Daily_bot/logs/market_traces_YYYYMMDD.csv`
- `Daily_bot/logs/account_traces_YYYYMMDD.csv`
- `Daily_bot/logs/trade_fills_audit.csv`
- `Daily_bot/logs/trade_fills_audit_daily.csv`
- `Daily_bot/logs/daily_rev.csv`

백테스트 산출물:

- `Daily_bot/backtest/results/*.csv`

## 실행

실거래:

```powershell
python .\Daily_bot\main.py --real
```

드라이런:

```powershell
python .\Daily_bot\main.py --dry-run
```

실거래 스크립트:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Daily_bot\scripts\run_real.ps1
```

테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest .\Daily_bot\tests
```

## 리플레이 해석 주의

- `bot.sqlite3`만으로는 과거 전체 세션이 다 남아 있지 않을 수 있다.
- 멀티데이 검증은 `logs/market_traces_*.csv`와 `logs/account_traces_*.csv` 기반으로 리플레이 DB를 다시 만드는 편이 낫다.
- 리플레이는 실거래의 부분체결, 취소 후 추가체결, 복구 매도주문 같은 실행 디테일을 완전하게 재현하지는 않는다.
- 따라서 실거래 손익과 백테스트 손익을 비교할 때는 `selected signals` 사용 여부와 체결 단순화 차이를 같이 봐야 한다.
