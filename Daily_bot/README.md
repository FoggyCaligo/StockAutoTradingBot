# Daily Bot

Daily Bot은 KOSPI200 종목을 대상으로 장중 초단기 기회를 추적하는 자동매매 봇이다.  
현재 기준 운용 방향은 "후보는 넓게 보되, 진입은 짧고 빠르게, 손절은 타이트하게, 기록은 나중에 복기 가능하도록 최대한 남긴다"에 가깝다.

## 현재 운용 시간

```yaml
market:
  prewarm_start_time: "08:55"
  start_buy_time: "09:30"
  stop_buy_time: "14:00"
  force_sell_time: "15:00"
  reconcile_time: "15:15"
  end_time: "15:20"
```

- `08:55 ~ 09:30`: 유니버스 준비, 후보 계산 준비, 세션 자본 계획 고정
- `09:30 ~ 14:00`: 신규 매수 가능
- `14:00 ~ 15:00`: 신규 매수 중단, 기존 포지션과 주문만 관리
- `15:00`: 남은 포지션 강제 청산 시작
- `15:15`: 브로커 체결 내역과 로컬 기록을 대조하는 EOD reconciliation 수행
- `15:20`: 세션 종료

## 현재 핵심 설정

```yaml
universe:
  min_market_cap_krw: 250000000000
  min_trading_value_krw: 3000000000

trend_filter:
  enabled: false

strategy:
  top_ratio: 0.20
  min_expected_return_percent: 0.30
  max_spread_percent: 0.7
  spread_expected_return_multiplier: 1.2
  max_prev_day_change_percent: 7.0
  max_intraday_jump_from_prev_scan_percent: 1.0
  sell_tick_offset: 1
  scan_interval_seconds: 60

risk:
  min_slot_count: 3
  slot_budget_unit_krw: 5000000
  max_slot_count: 10
  max_budget_per_stock_krw: 5000000
  max_orderbook_ask_depth_ratio: 0.30
  stop_loss_percent: 1.0
  daily_loss_limit_percent: 10.0
```

## 전략 요약

1. 장 시작 전 KOSPI200 유니버스를 준비한다.
2. 장중에는 20호가 스냅샷으로 예상가와 기대수익률을 계산한다.
3. 전체 후보 중 `top_ratio=0.20` 범위만 다음 필터로 넘긴다.
4. 최종 필터를 통과한 후보만 빈 슬롯 범위 안에서 매수한다.
5. 매수 직후 목표가 매도 주문을 걸고, 손절 또는 장 마감 청산으로 정리한다.

## 현재 필터 구조

- 유니버스: KOSPI200
- 시가총액 하한: `2500억`
- 거래대금 하한: `30억`
- 추세 필터: 현재 비활성화
- 전일 급등 제외: `+7.0%` 이상 제외
- 당일 급등 제외: 직전 스캔 대비 `+1.0%` 이상 제외
- 스프레드 상한: `0.7%`
- 기대수익률 하한: `max(0.3, spread_percent * 1.2)`
- 상위 후보 비율: `top_ratio = 0.20`

즉 단순히 `기대수익률 0.3% 이상`만 보는 구조가 아니라, 스프레드가 큰 종목일수록 더 높은 기대수익률을 요구한다.

## 자본 배분과 리스크

- 최소 슬롯 수: `3`
- 최대 슬롯 수: `10`
- 슬롯 기준 금액: `500만원`
- 종목당 최대 예산: `500만원`
- 현재 활성 손절선: `매수가 대비 -1.0%`
- 일일 손실 제한: `10.0%`

현재 자본 계획은 세션 시작 시점의 거래가능현금을 기준으로 잡고, 그 범위 안에서 슬롯 수와 종목당 예산을 고정한다.

## 호가잔량 필터

현재 실거래 로직에는 주문금액 대비 호가잔량 필터가 들어가 있다.

- 기준: `planned order amount <= top-5 ask depth amount * 0.30`
- 목적: 내 주문이 얇은 매도호가를 과도하게 먹는 상황 방지

주의:

- 이제 새로 쌓이는 `market_traces`에는 `ask_depth_5_amount_krw`가 함께 저장된다.
- 다만 과거 구간에는 이 값이 비어 있는 시점이 남아 있을 수 있으므로, 예전 백테스트는 여전히 완전 재현이 아니라 부분 재현으로 봐야 한다.
- 리플레이 엔진은 `--max-orderbook-ask-depth-ratio`와 `--missing-ask-depth-policy`를 통해 이 필터를 적용할 수 있고, 실행 시 ask-depth coverage를 함께 출력한다.
- 따라서 최근 리플레이 성과는 실제 실거래 로직보다 약간 낙관적으로 보일 여지가 있다.

## 매도 처리

- 일반 매도는 `예상가 - 1틱` 기준 목표가 주문을 사용한다.
- `15:00` 강제 청산은 지정가가 아니라 시장가 기준으로 정리한다.
- 부분체결 뒤 추가 매수 체결이 확인되면 남은 수량도 이어서 매도 주문에 반영되도록 보정되어 있다.

## 기록 체계

실거래 로그는 `Daily_bot/logs`에 저장한다.

- `fills_YYYYMMDD.csv`
- `orders_YYYYMMDD.csv`
- `market_traces_YYYYMMDD.csv`
- `account_traces_YYYYMMDD.csv`
- `trade_fills_audit.csv`

백테스트 결과 CSV는 `Daily_bot/backtest/results`에 별도로 저장한다.

## market_traces에 남기는 정보

현재 `market_traces`에는 아래 문맥이 함께 저장된다.

- `market_cap`
- `trading_value`
- `kospi_change_percent`
- `scan_cycle_at`
- 보유 추적용 `phase=active_position`

이 기록을 기반으로 후보군 축소 원인, 손절 직전 흔들림, 특정 날짜의 과도한 쏠림을 나중에 복기할 수 있다.

## 최근 복기 요약

- `11:30`, `13:00`보다 `14:00`까지 매수 가능 시간을 열어둔 쪽이 최근 `unselected` 리플레이에서 더 좋았다.
- 현재 설정값 기준 표준 리플레이는 총합 `+10.6058%`였다.
- 여기에 현재 실거래 로직에 더 가까운 `직전 스캔 급등 제외`까지 반영한 리플레이는 총합 `+11.0196%`였다.
- 다만 `호가잔량 비율 필터`는 과거 기록 부족으로 리플레이에 완전 반영되지 못했다.
- `2026-06-11` 수익은 `005090` 한 종목 기여가 컸고, 이 종목을 제외하면 해당 일자 손익은 `+10.6140%`에서 `+4.1196%`로 내려간다.

## 백테스트 해석 원칙

- 최근 구조에서는 `selected`보다 `unselected` 리플레이를 더 중요한 기준으로 본다.
- 이유는 필터와 자본 배분 구조가 많이 바뀌었기 때문에, 과거 `selected` 결과만으로 현재 전략을 평가하기 어렵기 때문이다.
- 최근 결과는 미래 수익을 보장하는 값이 아니라, 현재 설정이 어느 구간에서 어떤 식으로 작동했는지 보여주는 참고 기록이다.

## 실행

실거래:

```bash
python .\Daily_bot\main.py --real
```

시뮬레이션:

```bash
python .\Daily_bot\main.py --dry-run
```

백테스트:

```bash
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --db Daily_bot\bot.sqlite3 --ignore-selected-signals
```

테스트:

```bash
.\.venv\Scripts\python.exe -m pytest Daily_bot\tests
```
