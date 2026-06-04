# Codex Handoff — Kiwoom 20-Hoga Snapshot Ranking Bot

이 문서는 VS Code 내부의 Codex에게 바로 넘기기 위한 작업 지시서입니다.
현재 프로젝트는 **큰 틀만 잡힌 스켈레톤**이며, 목표는 키움 REST API를 실제로 연결하기 전까지 `--dry-run` / mock 환경에서 전략 흐름과 로그 구조를 먼저 안정화하는 것입니다.

---

## 0. 프로젝트 목적

KOSPI200 후보군 중 가격/시총/차트 필터를 통과한 종목을 대상으로, 각 종목의 **20호가 스냅샷을 1회 조회**하여 예상가와 예상수익률을 계산합니다.
그 후 예상수익률 상위 10% 후보를 뽑고, 최종 필터를 통과한 종목을 매수합니다.
매수 체결 즉시, 매수 당시 계산한 `expect_price - 1틱` 가격으로 지정가 매도 주문을 제출합니다.
13:00까지 미체결/보유 종목이 남아 있으면 기존 주문을 취소하고 시장가로 강제청산합니다.

핵심 전략은 다음과 같습니다.

```text
NO_POSITION
→ SCANNING
→ BUY_ORDER
→ BUY_FILLED
→ SELL_LIMIT_ORDER(expect_price - 1 tick)
→ WAIT_SELL_FILLED
   ├─ 전량 매도 체결 → NO_POSITION
   └─ 13:00 미체결/보유 → CANCEL_ALL → MARKET_SELL_ALL → NO_POSITION
```

이 봇은 계속 보유종목을 재판단하는 구조가 아닙니다.
**계좌에 주식이 없고 미체결 주문도 없을 때만 새 스캔을 시작**합니다.

---

## 1. 현재 폴더 구조

```text
kiwoom_hoga_snapshot_bot/
  main.py
  models.py
  utils.py
  requirements.txt
  .env.example

  broker/
    kiwoom_client.py      # 실제 키움 REST API 연결부 TODO
    mock_client.py        # dry-run용 mock broker

  strategy/
    universe.py           # KOSPI200/가격/시총/추세 후보군 생성 TODO 일부
    orderbook_predictor.py# 20호가 기반 예상가 계산
    signal.py             # 상위 10%, 최종 필터, 매수 대상 선정

  risk/
    guards.py             # 포지션/미체결/시간 조건 등 보호 로직
    force_sell.py         # 13:00 강제청산 흐름

  storage/
    db.py                 # SQLite 로그 저장

  config/
    settings.yaml

  docs/
    strategy_design.md
    CODEX_HANDOFF.md

  tests/
    test_predictor.py
```

---

## 2. Codex에게 우선 시킬 작업 순서

### Phase 1 — 스켈레톤이 실행되게 정리

1. `python main.py --dry-run`이 에러 없이 실행되는지 확인한다.
2. import path 문제, dataclass 필드 누락, 타입 불일치가 있으면 수정한다.
3. `pytest`가 실행되도록 테스트 환경을 정리한다.
4. mock client 기준으로 다음 흐름이 최소 1회 이상 도는지 확인한다.

```text
auth
→ account sync
→ candidate scan
→ 20-hoga snapshot
→ expected return calculation
→ top 10% selection
→ mock buy
→ mock buy filled
→ mock target sell order
```

Codex 작업 프롬프트 예시:

```text
Run this project in dry-run mode. Fix import/type/runtime errors only. Do not implement real Kiwoom endpoints yet. Keep the current architecture. After fixing, run pytest and show a short summary of changed files.
```

---

### Phase 2 — 전략 계산부 고정

`strategy/orderbook_predictor.py`를 중심으로 다음을 점검한다.

1. 20호가 입력 포맷을 명확히 고정한다.
2. 매수잔량/매도잔량 상쇄 알고리즘을 함수 단위로 분리한다.
3. `expect_price`가 유효 호가 단위에 맞게 반환되도록 한다.
4. `expect_price - 1틱`이 매수 체결가보다 낮거나 같으면 매수 대상에서 제외되도록 한다.
5. 테스트 케이스를 추가한다.

예상 함수 형태:

```python
def predict_price_from_hoga(hoga: HogaSnapshot) -> int:
    ...


def calc_expected_return(entry_price: int, expect_price: int) -> float:
    ...


def calc_target_sell_price(expect_price: int) -> int:
    return round_to_tick(expect_price - get_tick_size(expect_price))
```

Codex 작업 프롬프트 예시:

```text
Focus on strategy/orderbook_predictor.py. Make the 20-hoga input/output contract explicit. Add unit tests for predicted price, expected return, tick-size rounding, and the rule that target_sell_price must be greater than the expected buy price.
```

---

### Phase 3 — 후보군 생성부 구현

`strategy/universe.py`에서 실제 후보군 생성 로직을 구현한다.

초기 구현은 실제 키움 API 없이도 가능해야 합니다.
가능한 우선순위:

1. CSV 기반 후보군 입력 지원
2. FinanceDataReader 기반 KOSPI/KOSPI200 후보군 수집
3. 가격 필터
4. 시가총액 필터
5. 거래대금 필터
6. 차트 우상향 필터

우상향 필터 기본값:

```text
current_price > MA20
MA5 > MA20
MA20 slope > 0
```

주의:
FinanceDataReader에서 KOSPI200 구성 종목을 직접 안정적으로 제공하지 못할 수 있습니다.
그 경우 첫 버전은 `data/kospi200.csv`를 기준으로 동작하게 만들고, FDR은 가격/일봉 보조 데이터 수집에 사용합니다.

Codex 작업 프롬프트 예시:

```text
Implement a CSV-first universe loader. Add optional FinanceDataReader support only if available. The bot must still run in dry-run mode without network. Add data/kospi200_sample.csv for tests.
```

---

### Phase 4 — 주문 상태 머신 보강

`main.py`, `risk/guards.py`, `risk/force_sell.py` 중심으로 상태 흐름을 명확히 한다.

필수 상태:

```text
NO_POSITION
SCANNING
BUYING
WAIT_BUY_FILLED
SELLING
WAIT_SELL_FILLED
FORCE_SELLING
STOPPED
```

단, 전략적으로는 HOLDING 판단 로직이 필요 없습니다.
매수 체결 직후 목표 지정가 매도 주문이 들어가므로, 보유 중 재판단은 하지 않습니다.
하지만 시스템상 `WAIT_SELL_FILLED` 상태는 반드시 필요합니다.

강제청산 규칙:

```text
13:00 도달
→ open orders 조회
→ 모든 미체결 주문 취소
→ 취소 완료 확인
→ positions 조회
→ 남은 보유 수량 시장가 매도
→ 전량 매도 확인
→ STOPPED 또는 NO_POSITION
```

실전 안전상 13:00 이후에는 신규매수하지 않습니다.

Codex 작업 프롬프트 예시:

```text
Refactor main.py into an explicit state-machine flow. Do not add holding strategy logic. After buy fill, immediately place a target limit sell at expect_price minus one tick. Add force sell at 13:00: cancel open orders, wait for cancellation, then market sell all positions.
```

---

### Phase 5 — 실제 키움 REST API 연결부

`broker/kiwoom_client.py`는 현재 TODO 상태입니다.
공식 문서 기준으로만 구현합니다.

구현 대상:

```text
auth()
refresh_token_if_needed()
get_20hoga(ticker)
buy_limit(ticker, quantity, price)
sell_limit(ticker, quantity, price)
sell_market(ticker, quantity)
cancel_order(order_id)
cancel_all_open_orders()
get_order_status(order_id)
get_positions()
get_open_orders()
get_account_balance()
```

중요 원칙:

1. 실제 endpoint, header, body field는 키움 공식 REST API 가이드를 기준으로 채운다.
2. 주문/조회 제한이 있으면 반드시 rate limiter를 둔다.
3. 주문 함수는 raw response를 그대로 로그에 저장할 수 있게 반환한다.
4. 매수 체결 확인 전에는 매도 주문을 넣지 않는다.
5. 매도 주문 취소 완료 확인 전에는 시장가 매도를 넣지 않는다.

Codex 작업 프롬프트 예시:

```text
Implement broker/kiwoom_client.py using the official Kiwoom REST API docs. Keep all endpoint names and TR IDs in constants. Add a simple rate limiter for REST calls. Do not change strategy logic. Add clear TODO comments for fields that require verification from the official docs.
```

---

## 3. 설정값 정책

`config/settings.yaml`에서 관리해야 할 값:

```yaml
trading:
  dry_run: true
  market_open: "09:00"
  buy_start: "09:10=-3
  buy_end: "11:30"
  force_sell_time: "13:00"
  max_buy_count: 3
  top_ratio: 0.10
  min_expected_return_percent: 1.0
  max_spread_percent: 0.3
  max_position_count: 3

universe:
  price_min: 10000
  price_max: 50000
  market_cap_min: 300000000000
  use_trend_filter: true

risk:
  per_trade_budget_ratio: 0.33
  daily_max_loss_krw: 10000
  allow_rebuy_after_force_sell: false

api:
  rate_limit_per_sec: 5
```

---

## 4. 로그/DB 요구사항

모든 판단은 나중에 검증 가능해야 합니다.
SQLite에 최소 다음 테이블이 필요합니다.

```text
cycles
- id
- started_at
- ended_at
- candidate_count
- selected_count
- status

snapshots
- id
- cycle_id
- ticker
- snapshot_time
- current_price
- expect_price
- expect_return_percent
- spread_percent
- raw_hoga_json

signals
- id
- cycle_id
- ticker
- rank
- passed_final_filter
- reason

orders
- id
- cycle_id
- ticker
- side
- order_type
- price
- quantity
- broker_order_id
- status
- raw_response_json
- created_at

fills
- id
- order_id
- ticker
- side
- fill_price
- fill_quantity
- fee
- tax
- filled_at

errors
- id
- where
- message
- raw_json
- created_at
```

---

## 5. 금지/주의 사항

Codex가 작업할 때 지켜야 할 제약입니다.

1. 실제 매매 기본값은 항상 `dry_run: true`로 둔다.
2. `.env`나 API Key를 커밋하지 않는다.
3. 주문 관련 함수에서 실패를 조용히 무시하지 않는다.
4. 실계좌 주문 로직은 mock 테스트 통과 전까지 활성화하지 않는다.
5. 13:00 강제청산 로직은 제거하지 않는다.
6. 계좌에 주식 또는 미체결 주문이 있으면 새 스캔을 시작하지 않는다.
7. `expect_price - 1틱 <= 실제 매수 체결가`이면 즉시 매도 목표가가 성립하지 않으므로 매수 자체를 막거나 즉시 청산한다.
8. 후보군 전체를 무조건 사지 않는다. 상위 10% + 절대 기대수익률 + 최종 필터를 모두 통과해야 한다.
9. REST API rate limit 초과를 방치하지 않는다.
10. 전략 로직과 broker API 연결부를 섞지 않는다.

---

## 6. 추천 첫 작업 프롬프트

VS Code Codex에 처음 던질 프롬프트:

```text
You are working on a Python trading bot skeleton named kiwoom_hoga_snapshot_bot.

Goal for this task:
Make the current skeleton executable in dry-run mode and testable, without implementing real Kiwoom REST endpoints yet.

Important strategy rules:
- The bot scans only when there are no positions and no open orders.
- It scans KOSPI200 candidates, fetches one 20-hoga snapshot per ticker, calculates expected price/return, selects top 10%, applies final filters, buys selected targets, then immediately places a target limit sell at expect_price minus one tick after buy fill.
- No holding strategy logic is needed.
- At 13:00, cancel all open orders, confirm cancellation, then market sell all remaining positions.
- Keep dry_run as the default.

Tasks:
1. Run tests and dry-run entrypoint.
2. Fix import/runtime/type errors.
3. Make orderbook predictor contracts explicit.
4. Add or update tests for tick-size, expected return, top-10 selection, and force-sell flow using mock broker.
5. Do not connect real API keys or real endpoints.
6. Summarize changed files and remaining TODOs.
```

---

## 7. 참고 문서

- OpenAI Codex IDE extension 공식 문서: VS Code, Cursor, Windsurf 등 IDE에서 Codex를 사용할 수 있으며, Codex가 코드 읽기/수정/실행을 돕는다는 설명이 있다.
- 키움 REST API 공식 사이트/가이드: 실제 endpoint, TR ID, header, body field는 반드시 공식 문서 기준으로 확인해야 한다.

