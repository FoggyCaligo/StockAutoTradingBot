# Daily Bot

Daily Bot은 KOSPI200 종목을 대상으로 장중 단기 매매 기회를 스캔하고, 조건을 통과한 종목만 제한적으로 진입하는 실거래 봇이다.

현재 기준 운영 설정은 [config/settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)을 단일 진실원천으로 본다. 문서와 코드가 다르면 설정 파일이 우선이다.

## 현재 운영 시간

```yaml
market:
  prewarm_start_time: "08:55"
  start_buy_time: "09:10"
  stop_buy_time: "11:30"
  force_sell_time: "15:00"
  reconcile_time: "15:15"
  end_time: "15:20"
```

- `08:55 ~ 09:10`: 유니버스 준비, 기준가 저장, 세션 자본 계획 고정
- `09:10 ~ 11:30`: 신규 매수 가능
- `11:30 ~ 15:00`: 신규 매수 중단, 보유 포지션과 주문만 관리
- `15:00`: 강제 청산 시작
- `15:15`: 브로커 체결과 로컬 기록 대조
- `15:20`: 세션 종료

## 현재 핵심 설정

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
  min_expected_return_percent: 0.3
  max_spread_percent: 0.0
  spread_expected_return_multiplier: 0.0
  min_prev_day_change_percent: -1.0
  max_prev_day_change_percent: 0.0
  max_intraday_jump_from_prev_scan_percent: 0.0
  sell_tick_offset: 1
  scan_interval_seconds: 60

risk:
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

## 현재 진입 로직

1. `KOSPI200` 전체를 불러온다.
2. 시가총액과 거래대금 하한만 적용한다.
3. 20호가 기반으로 기대가격과 기대수익률을 계산한다.
4. 아래 조건을 모두 통과한 종목만 최종 후보로 본다.

- 기대수익률 `>= 0.3%`
- 전일 등락률 `<= 0.0%`
- 전일 등락률 `<= -1.0%`도 만족해야 함
- 목표 매도가가 현재가보다 높아야 함

현재 `max_spread_percent = 0.0`, `max_intraday_jump_from_prev_scan_percent = 0.0`, `max_orderbook_ask_depth_ratio = 0.0`이므로 이 세 필터는 사실상 꺼져 있다.

## 자본 배분

- 세션 시작 시점의 거래가능현금을 기준으로 슬롯 계획을 고정한다.
- 기본 슬롯 단위는 `500만원`이다.
- 최소 슬롯 수는 `3`, 최대 슬롯 수는 `10`이다.
- `max_buy_count = 3`이므로 한 번의 진입 사이클에서 동시에 새로 사는 종목 수는 최대 3개다.

## 청산 로직

- 매수 체결 직후 목표가 지정가 매도 주문을 건다.
- 장중 손절 설정값은 남아 있지만 현재는 `stop_loss_tick_count = 0`, `stop_loss_tick_multiplier = 0.0` 상태라 동적 손절은 사실상 꺼져 있다.
- `15:00` 이후에는 신규 진입 없이 강제 청산 흐름으로 넘어간다.
- `15:15`에 브로커 체결을 다시 읽어 `fills`와 CSV를 보정한다.
- EOD 대조 시 `sell_reconciliation` 추정 매도 체결은 실제 브로커 체결로 대체되면 정리된다.

## 로그와 산출물

운영 로그:

- `Daily_bot/logs/orders_YYYYMMDD.csv`
- `Daily_bot/logs/fills_YYYYMMDD.csv`
- `Daily_bot/logs/market_traces_YYYYMMDD.csv`
- `Daily_bot/logs/account_traces_YYYYMMDD.csv`
- `Daily_bot/logs/trade_fills_audit.csv`
- `Daily_bot/logs/trade_fills_audit_daily.csv`
- `Daily_bot/logs/daily_rev.csv`

백테스트 결과:

- `Daily_bot/backtest/results/*.csv`

## 실행

실운영:

```powershell
python .\Daily_bot\main.py --real
```

드라이런:

```powershell
python .\Daily_bot\main.py --dry-run
```

실운영 스크립트:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Daily_bot\scripts\run_real.ps1
```

테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest .\Daily_bot\tests
```

리플레이 백테스트:

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --db Daily_bot\bot.sqlite3 --ignore-selected-signals
```

## 리플레이 해석 주의

- `bot.sqlite3`는 현재 세션 데이터만 남아 있을 수 있다.
- 멀티데이 리플레이는 `Daily_bot/logs/market_traces_*.csv`를 사용해 재구성해야 한다.
- 오래된 로그에는 `prev_day_change_percent`가 없어서, 현재의 전일등락률 필터를 그대로 적용하면 과거 날짜가 전부 탈락할 수 있다.
- 따라서 현재 설정을 과거 로그에 그대로 대입한 리플레이 결과는 “완전 동일 재현”이 아니라 “현재 필터를 과거 로그에 투영한 근사 결과”로 봐야 한다.
