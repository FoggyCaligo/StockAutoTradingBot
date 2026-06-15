# Daily Bot

Daily Bot은 키움 REST API 기반의 KOSPI200 초단타 자동매매 봇이다.  
기본 방향은 "후보군은 너무 일찍 좁히지 않고, 장중에는 짧게 진입하고, 빠르게 익절하거나 손절하며, 기록은 최대한 검증 가능하게 남긴다"에 가깝다.

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

- `08:55 ~ 09:30`
  유니버스 예열, 후보군 준비, 세션 슬롯 수와 슬롯당 자본금 계산 및 고정
- `09:30 ~ 14:00`
  신규 매수 가능 구간
- `14:00 ~ 15:00`
  신규 매수 중단, 기존 포지션/주문만 관리
- `15:00`
  강제 청산 시작. 남은 포지션은 시장가 기준으로 정리한다.
- `15:15`
  브로커 체결 내역과 로컬 기록을 EOD reconciliation으로 대조
- `15:20`
  세션 종료

기존 문서에서는 `13:00` 종료를 기준으로 설명하던 부분이 있었지만, 현재는 실거래와 백테스트 모두 `14:00` 기준으로 맞춰져 있다.

## 현재 핵심 전략

1. 예열 시간대에 KOSPI200 후보군을 준비한다.
2. 거래가능금액 기준으로 세션 슬롯 수와 슬롯당 자본금을 계산하고, 장중에는 그 값을 고정해서 사용한다.
3. 장중에는 20호가 스냅샷 기반 가격예상 알고리즘으로 기대수익률을 계산한다.
4. 전체 스캔 결과를 순위화한 뒤 `top_ratio`와 최종 필터를 적용해 후보를 추린다.
5. 기대수익률이 높고 조건을 통과한 후보를 빈 슬롯 수만큼 매수한다.
6. 매수 체결 직후 목표가 지정가 매도주문을 넣는다.
7. 손절 기준에 닿으면 즉시 손절매를 실행한다.
8. `14:00` 이후에는 신규 매수 없이 기존 포지션만 관리한다.
9. `15:00` 이후에는 남은 포지션을 정리하고, `15:15` 이후 브로커 체결 기준으로 기록을 확정한다.

중요:

- 실거래 로직은 `selected 전용 후보`만 사는 구조가 아니다.
- 현재 전략 평가 기준은 `selected`보다 `unselected` 백테스트를 우선한다.

## 현재 주요 설정

```yaml
universe:
  min_market_cap_krw: 250000000000
  min_trading_value_krw: 3000000000

trend_filter:
  enabled: false

strategy:
  top_ratio: 0.30
  min_expected_return_percent: 0.30
  max_spread_percent: 0.5
  max_prev_day_change_percent: 0.0
  sell_tick_offset: 1
  scan_interval_seconds: 60

risk:
  max_position_count: 0
  min_slot_count: 5
  slot_budget_unit_krw: 5000000
  max_slot_count: 10
  max_budget_per_cycle_krw: 0
  max_budget_per_stock_krw: 5000000
  stop_loss_percent: 3.0
  daily_loss_limit_percent: 10.0
```

해석:

- `min_market_cap_krw: 2500억`
  과거보다 완화된 시가총액 기준이다.
- `min_trading_value_krw: 30억`
  거래대금 기준도 현재는 완화된 상태다.
- `trend_filter.enabled: false`
  추세 필터는 현재 비활성화 상태다.
- `top_ratio: 0.30`
  스캔 결과 상위 30%만 다음 필터로 넘긴다.
- `min_expected_return_percent: 0.30`
  기대수익률 0.3% 미만 후보는 제외한다.
- `max_spread_percent: 0.5`
  스프레드가 너무 넓은 후보를 줄인다.
- `max_prev_day_change_percent: 0.0`
  현재는 사실상 전영업일 급등 필터를 사용하지 않는 해석으로 운용한다.
- `min_slot_count: 5`
  자본이 작아도 기본 슬롯 수는 5개다.
- `max_slot_count: 10`
  슬롯 수는 최대 10개까지만 늘어난다.
- `stop_loss_percent: 3.0`
  현재 운영 및 백테스트 기준 손절선은 `-3.0%`다.

## 슬롯/자본 배분 방식

현재는 예열 시점에 `거래가능금액`을 기준으로 세션 계획을 고정한다.

- 슬롯 수 계산 기준: 거래가능금액
- 슬롯당 자본금 계산 기준: 거래가능금액 / 슬롯 수
- 장중에 현금이 줄거나 늘어도, 슬롯 수와 슬롯당 자본금 자체는 세션 시작 시 기준값을 유지한다.

또한 자본이 커져도 슬롯 수는 무한정 늘리지 않고 `10개`에서 상한을 둔다.  
이 방향은 "후보는 넓게 보되, 종목 수는 통제한다"는 최근 운용 철학과 연결된다.

## 매수 후 매도 처리

- 매수 체결 직후 목표가 매도주문을 넣는다.
- 다만 `15:00` 강제청산은 당일 포지션을 반드시 비우는 목적이므로 지정가가 아니라 시장가로 처리한다.
- 부분체결 후 취소된 주문에서 나중에 추가 매수 체결이 확인되면, 그 추가 수량에 대해서도 후속 매도주문이 자동으로 들어가도록 보정되어 있다.
- 즉, "처음 부분체결 수량만 매도주문이 걸리고 나머지가 방치되는 문제"는 수정된 상태다.

## 손절매

기본적으로 봇은 보유 중 종목을 계속 감시하며 손절 조건에 닿으면 즉시 손절한다.

현재 기준:

- 기본 손절선은 `매수가 대비 -3.0%`
- 이 값은 완전 확정된 영구 최적값이라기보다, 최근 복기와 리플레이에서 가장 균형이 좋았던 운영 기준선이다.

## 기록 체계

### DB

- `bot.sqlite3`
  핵심 원본 기록
- `fills`
  체결 기록 원본
- `orders`
  주문 기록
- `market_traces`
  스캔/감시/보유 중 가격 추적
- `account_traces`
  계좌 상태 추적

### CSV

실거래 로그는 `Daily_bot/logs`에 남긴다.

- `fills_YYYYMMDD.csv`
- `orders_YYYYMMDD.csv`
- `market_traces_YYYYMMDD.csv`
- `account_traces_YYYYMMDD.csv`
- `trade_fills_audit.csv`
- 실행 로그 및 lock 파일

백테스트 CSV는 `Daily_bot/backtest/results`에 별도로 둔다.

## `trade_fills_audit.csv` 정책

이 파일은 "검증용 실제 체결 기록"에 가깝다.

- 포함: 실제 확인된 매수/매도 체결
- 제외: `position_recovery`, `sell_reconciliation` 같은 추정성 보정 기록

대신 추정성 보정 기록도 DB `fills`에는 남겨 둔다.  
즉, `trade_fills_audit.csv`는 보여주기 좋은 감사용, DB는 복구와 분석까지 포함한 원본에 가깝다.

## `market_traces` 추가 기록

현재 `market_traces`에는 예전보다 더 많은 문맥을 남긴다.

- `market_cap`
- `trading_value`
- `kospi_change_percent`
- `scan_cycle_at`
- 보유 추적용 `phase=active_position`

이 기록을 이용하면 나중에:

- 특정 필터가 실제로 후보를 얼마나 잘라냈는지
- 손절선 후보별 민감도
- 보유 중 흔들림의 크기

를 더 정확하게 분석할 수 있다.

## 백테스트 해석 원칙

현재 전략 튜닝에서는 `selected`보다 `unselected`를 기준으로 본다.

이유:

- 필터 구조를 최근 많이 손봤기 때문에, 과거 `selected` 기준 결과는 현재 후보군 변화와 잘 맞지 않을 수 있다.
- 실제 운영 로직도 `selected 전용 매수`가 아니라 전체 후보를 좁혀 들어가는 구조다.

최근 관찰:

- 최신 리플레이 엔진 기준에서는 `stop_buy_time`을 `11:30`, `13:00`보다 `14:00`까지 열어두는 쪽이 더 좋은 결과를 보였다.

이 값은 어디까지나 최근 기록 구간 기준 실험 결과이지, 미래 수익을 보장하는 값은 아니다.

## EOD reconciliation

`15:15` 이후에는 브로커 체결 내역과 로컬 기록을 다시 대조한다.

- 누락된 체결 보완
- 잘못 기록된 가격/시간/source 보정
- DB 기준으로 일별 체결 CSV와 감사 CSV 재구성

따라서 장중 로그보다 `15:15` 이후 정리된 DB/CSV가 최종 결과에 더 가깝다.

## 실행

실거래:

```bash
python .\Daily_bot\main.py --real
```

드라이런:

```bash
python .\Daily_bot\main.py --dry-run
```

백테스트:

```bash
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --db Daily_bot\bot.sqlite3 --ignore-selected-signals
```

## 테스트

```bash
.\.venv\Scripts\python.exe -m pytest Daily_bot\tests
```



현재 코드는 대략 이렇게 돈다.

09:30부터 14:00까지 신규매수하고, 15:00에 강제청산한다.
후보군은 KOSPI200 기반으로 매일 갱신하고, 시총 2500억 이상, 거래대금 30억 이상만 남긴다. 추세 필터는 꺼져 있다.
그다음 호가 20단계를 보고 예상가를 계산하고, 기대수익률 기준 상위 30%만 본 뒤, 기대수익률 0.3% 이상·스프레드 0.5% 이하인 후보를 통과시킨다.
실제 주문은 현재가/호가 기반으로 산정된 후보를 대상으로, 슬롯 예산 안에서 살 수 있는 종목만 골라 들어간다.