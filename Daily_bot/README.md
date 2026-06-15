# Daily Bot

Daily Bot은 키움 REST API 기반의 KOSPI200 초단타 자동매매 봇이다.  
기본 방향은 "짧게 진입하고, 빠르게 익절하거나 손절하며, 기록은 최대한 검증 가능하게 남긴다"에 가깝다.

## 현재 운용 시간

```yaml
market:
  prewarm_start_time: "08:55"
  start_buy_time: "09:30"
  stop_buy_time: "13:00"
  force_sell_time: "15:00"
  reconcile_time: "15:15"
  end_time: "15:20"
```

- `08:55 ~ 09:30`
  유니버스 예열, 후보군 준비, 세션 슬롯 수와 슬롯당 자본금 계산 및 고정
- `09:30 ~ 13:00`
  신규 매수 가능 구간
- `13:00 ~ 15:00`
  신규 매수 중단, 기존 포지션/주문만 관리
- `15:00`
  강제 청산 시작
- `15:15`
  브로커 체결 내역과 로컬 기록을 EOD reconciliation으로 대조
- `15:20`
  세션 종료

## 현재 핵심 전략

1. 예열 시간대에 KOSPI200 후보군을 준비한다.
2. 거래가능금액 기준으로 세션 슬롯 수와 슬롯당 자본금을 계산하고, 장중에는 그 값을 고정해서 사용한다.
3. 장중에는 20호가 스냅샷 기반 가격예상 알고리즘으로 기대수익률을 계산한다.
4. 기대수익률이 높은 후보를 추려 빈 슬롯 수만큼 매수한다.
5. 매수 체결 직후 목표가 지정가 매도주문을 넣는다.
6. 손절 기준에 닿으면 즉시 손절매를 실행한다.
7. `13:00` 이후에는 신규 매수 없이 기존 포지션만 관리한다.
8. `15:00` 이후에는 남은 포지션을 정리하고, `15:15` 이후 브로커 체결 기준으로 기록을 확정한다.

## 현재 주요 설정

```yaml
strategy:
  top_ratio: 0.30
  min_expected_return_percent: 0.3
  max_spread_percent: 0.7
  max_prev_day_change_percent: 100.0
  sell_tick_offset: 1
  scan_interval_seconds: 60

risk:
  max_position_count: 0
  min_slot_count: 5
  slot_budget_unit_krw: 5000000
  max_slot_count: 10
  max_budget_per_cycle_krw: 0
  max_budget_per_stock_krw: 5000000
  stop_loss_percent: 1.0
  daily_loss_limit_percent: 10.0
```

해석:

- `top_ratio: 0.30`
  스캔 결과 상위 30%만 다음 필터로 넘긴다.
- `min_expected_return_percent: 0.3`
  기대수익률 0.3% 미만 후보는 제외한다.
- `max_prev_day_change_percent: 100.0`
  전영업일 급등 필터는 사실상 비활성화 상태다.
- `min_slot_count: 5`
  자본이 작아도 기본 슬롯 수는 5개다.
- `slot_budget_unit_krw: 5000000`
  500만원당 1슬롯 개념으로 슬롯 수를 계산한다.
- `max_slot_count: 10`
  슬롯 수는 최대 10개까지만 늘어난다.
- `max_budget_per_stock_krw: 5000000`
  종목당 하드캡은 500만원이다.
- `stop_loss_percent: 1.0`
  현재 값은 내일 하루 실험용으로 수동 조정된 값이다.

## 슬롯/자본 배분 방식

현재는 예열 시점에 `거래가능금액`을 기준으로 세션 계획을 고정한다.

- 슬롯 수 계산 기준: 거래가능금액
- 슬롯당 자본금 계산 기준: 거래가능금액 / 슬롯 수
- 장중에 현금이 줄거나 늘어도, 슬롯 수와 슬롯당 자본금 자체는 세션 시작 시 기준값을 유지한다.

예시:

- 거래가능금액 `180만원`
- 슬롯 수 `3개`
- 슬롯당 자본금 `60만원`

## 매수 후 매도 처리

- 매수 체결 직후 목표가 매도주문을 넣는다.
- 부분체결 후 취소된 주문에서 나중에 추가 매수 체결이 확인되면, 그 추가 수량에 대해서도 후속 매도주문이 자동으로 들어가도록 보정되어 있다.
- 즉, "처음 부분체결 수량만 매도주문이 걸리고 나머지가 방치되는 문제"는 수정된 상태다.

## 손절매

기본적으로 봇은 보유 중 종목을 계속 감시하며 손절 조건에 닿으면 즉시 손절한다.

중요:

- 현재 `1.0%` 손절은 정식 최적값으로 확정한 것이 아니다.
- 내일 하루 동안 "실험 목적"으로만 타이트하게 내려서 돌려보는 설정이다.
- 목적은 수익 극대화보다, 초단타 전략에서 지나치게 짧은 손절선이 어떤 결과를 만드는지 관찰하는 데 있다.

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

- `fills_YYYYMMDD.csv`
  일자별 체결 CSV
- `trade_fills_audit.csv`
  실제 체결만 남기는 감사용 CSV
- `daily_rev.csv`
  일자별 손익 요약 CSV
- `market_traces_YYYYMMDD.csv`
  스캔/감시/보유 중 시세 추적 CSV
- `account_traces_YYYYMMDD.csv`
  계좌 상태 추적 CSV

## `trade_fills_audit.csv` 정책

이 파일은 "검증용 실제 체결 기록"에 가깝다.

- 포함: 실제 확인된 매수/매도 체결
- 제외: `position_recovery`, `sell_reconciliation` 같은 추정성 보정 기록

대신 추정성 보정 기록도 DB `fills`에는 남겨 둔다.  
즉, `trade_fills_audit.csv`는 보여주기 좋은 감사용, DB는 복구와 분석까지 포함한 원본에 가깝다.

## `daily_rev.csv`

장 마감 이후 일자별로 다음 값을 한 줄에 기록한다.

- 시작 자본금
- 총 수익금
- 총 수수료
- 총 세금
- 총 매수금
- 총 매도금
- 총 수익률
- 거래 종목 목록

## 보유 중 가격 추적

손절 기준을 나중에 정교하게 조정할 수 있도록, 보유 종목은 `market_traces`에 별도로 더 남긴다.

- `phase=active_position`
- `reason=held_position_monitor qty=...`

이 기록을 이용하면 나중에:

- 익절 성공 종목의 최대 역행폭(MAE)
- 손절선 후보별 민감도
- "익절은 됐지만 중간에 얼마나 흔들렸는가"

를 더 정확하게 분석할 수 있다.

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

## 테스트

```bash
.\.venv\Scripts\python.exe -m pytest Daily_bot\tests
```

## 메모

- 현재 손절 `1.0%`는 실험값이다.
- 실험이 끝나면 결과를 보고 다시 `2.5%`, `2.0%`, `1.5%` 등과 비교 검토하는 것이 맞다.
- 평균 수익률 판단은 과거 로그 포맷 변경, 복구, 재시작 이력 때문에 반드시 최근 일관된 구간만 따로 봐야 한다.
