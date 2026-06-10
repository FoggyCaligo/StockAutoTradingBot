# Daily Bot

Daily Bot은 키움 REST API를 이용해 KOSPI200 후보군을 스캔하고, 20호가 기반 예상가를 바탕으로 당일 단타를 수행하는 자동매매 봇입니다.

현재 운영 기준은 다음 두 가지입니다.

- 전략 잠재수익률은 과소평가하지 않는다.
- 체결, 손익, 감사용 기록은 브로커 원본 기준으로 엄격하게 검증한다.

## 핵심 운영 시간

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
  유니버스 준비와 후보 스캔용 워밍업 구간입니다.
- `09:30 ~ 13:00`
  신규 매수와 체결 기반 목표매도 관리가 이뤄집니다.
- `13:00 ~ 15:00`
  신규 매수는 중단하고 기존 보유/주문만 관리합니다.
- `15:00`
  미체결 주문을 정리하고 보유분을 강제청산합니다.
- `15:15`
  키움 브로커 체결내역과 로컬 기록을 일괄 대조합니다.
- `15:20`
  대조 완료 후 세션을 종료합니다.

## 전략 요약

### 1. 유니버스 구성

- 기본 대상: `KOSPI200`
- 시가총액 하한: `3000억 원`
- 거래대금 하한: `50억 원`
- 추세 필터: `trend_filter.enabled: true`
- 데이터 소스:
  - 우선 `data/kospi200_latest.csv`
  - 필요 시 `data/kospi200.csv`

### 2. 예상가 계산

각 종목에 대해 20호가 스냅샷을 한 번 조회한 뒤, 매수/매도 잔량 균형이 맞는 구간을 찾고 그 구간의 중간값을 예상가로 사용합니다.

- 목표 매도가: `expect_price - 1 tick`
- 기대수익률:

```text
(target_sell_price - current_price) / current_price * 100
```

### 3. 매수 후보 필터

현재 기본 설정:

```yaml
strategy:
  top_ratio: 0.20
  max_buy_count: 0
  min_expected_return_percent: 0.25
  max_spread_percent: 0.7
  sell_tick_offset: 1
  scan_interval_seconds: 60
```

주요 조건:

- 기대수익률 `0.25%` 이상
- 스프레드 `0.7%` 이하
- `target_sell_price > current_price`
- 이미 보유 중이거나 미체결 주문이 걸린 종목 제외
- 현재 주문가능현금으로 1주 이상 매수 가능해야 함

### 4. 자금 배분

현재 기본 설정:

```yaml
risk:
  min_slot_count: 3
  target_budget_ratio_per_stock: 0.33
  max_budget_per_cycle_krw: 0
  max_budget_per_stock_krw: 5000000
  max_position_count: 0
```

해석:

- 최소 3슬롯 기준으로 예산을 나눕니다.
- 종목당 목표 비중은 약 33%입니다.
- 종목당 최대 500만 원을 넘기지 않습니다.
- `max_buy_count: 0`, `max_position_count: 0`은 고정 개수 제한 없이 자금과 조건에 따라 유연하게 진입한다는 뜻입니다.

## 주문/체결 흐름

```text
NO_POSITION
-> SCANNING
-> BUY_ORDER
-> WAIT_BUY_FILLED
-> SELL_LIMIT_ORDER
-> WAIT_SELL_FILLED
-> NO_POSITION

15:00 이후
-> CANCEL_OPEN_ORDERS
-> MARKET_SELL_ALL
-> 15:15 EOD_RECONCILIATION
-> STOPPED
```

핵심 규칙:

- 매수 체결 전에는 매도 주문을 넣지 않습니다.
- 매수 체결 직후 목표 지정가 매도 주문을 넣습니다.
- 손절 조건 도달 시 시장가 매도로 청산할 수 있습니다.
- `15:00` 이후에는 신규 매수를 하지 않습니다.

## 체결기록과 감사용 로그

### 원장 우선순위

신뢰 우선순위는 아래와 같습니다.

1. 키움 브로커 원본 응답
2. SQLite `fills` 테이블
3. `fills_YYYYMMDD.csv`
4. `trade_fills_audit.csv`

### 체결 저장 방식

- 장중에는 `ka10076` 체결조회로 주문별 체결을 직접 찾습니다.
- `ka10076`의 `ord_no`는 정확한 주문번호 필터가 아니라 과거 조회용 커서이므로, 당일 체결목록을 조회한 뒤 로컬에서 주문번호를 매칭합니다.
- 분할체결이면 가중평균 체결가를 저장합니다.
- 수수료/세금은 가능하면 브로커 원본 `tdy_trde_cmsn`, `tdy_trde_tax`를 사용합니다.

### 15:15 마감 대조

장 마감 후에는 브로커 체결 전체를 다시 조회해 로컬 체결 원장을 정정합니다.

- 빠진 `SELL` 체결을 채웁니다.
- 잘못 저장된 가격/시간/source를 덮어씁니다.
- 그 뒤 `fills_YYYYMMDD.csv`와 `trade_fills_audit.csv`를 DB 기준으로 다시 생성합니다.

즉, 장중 기록보다 `15:15` 이후 기록이 더 신뢰도가 높습니다.

### `trade_fills_audit.csv` 주의사항

이 파일은 감사용 누적 원장입니다.

- 종목별 누적 매수/매도/손익 상태를 담습니다.
- 가족이나 제3자가 엑셀로 필터링해 보기 좋게 만든 파일입니다.
- `SELL` 행을 단순 합산하면 종목별 누적값이 중복되어 총손익이 과대계산될 수 있습니다.

따라서 이 파일은 "거래 증빙" 용도로 좋고, "총손익 집계"는 별도 요약 로직이나 브로커 API와 함께 봐야 합니다.

## 장중 숫자와 마감 후 숫자

- 장중 손익: `MTS 우선`
- 마감 후 체결/감사 기록: `15:15 대조 후 로컬 기록 우선`

현재 운영 원칙:

- 전략 성과는 과소평가하지 않는다.
- 숫자는 MTS, 브로커 응답, DB, CSV를 서로 대조하면서 검증한다.

## 과거 성과 확인

과거 성과는 아래 API 조합으로 확인합니다.

- `ka10074`
  기간 실현손익, 매수/매도금액, 수수료, 세금
- `kt00002`
  기간별 예수금/추정예탁자산 추이
- `kt00005`
  현재 평가손익과 보유 포지션 상태
- `ka10076`
  당일 체결 상세

권장 해석:

- 기간 실현손익: `ka10074`
- 현재 실현+미실현 총손익: `ka10074 + kt00005`
- 시작자산 대비 수익률: `kt00002` 첫날 예수금 또는 추정예탁자산 기준

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

## 운영 팁

- `Daily_bot/logs/run_real.lock`에서 현재 실행 PID를 확인할 수 있습니다.
- 장중에는 로그보다 MTS 숫자를 우선 확인하는 편이 안전합니다.
- 마감 후 기록 검증은 `fills_YYYYMMDD.csv`, `trade_fills_audit.csv`, `bot.sqlite3`의 `fills` 테이블 순서로 확인하면 됩니다.
