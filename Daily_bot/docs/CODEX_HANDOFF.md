# Codex Handoff - Daily Bot

이 문서는 다음 작업자가 현재 `Daily_bot`을 빠르게 이해하고, 코드 수정 시 무엇을 보존해야 하는지 파악하도록 돕는 운영용 인수인계 문서입니다.

## 1. 현재 봇의 정체성

이 봇은 단순 스켈레톤이나 목업이 아니라, 실제 장중 운용과 체결 복원까지 포함한 실거래 봇입니다.

- 키움 REST API를 실거래에 사용한다.
- 장중 체결은 `ka10076` 기반으로 기록한다.
- 장 마감 후 `15:15`에 브로커 체결과 로컬 원장을 재대조한다.
- 전략 평가는 다소 공격적으로 볼 수 있어도, 기록과 회계 숫자는 매우 보수적으로 검증한다.

## 2. 운영 타임라인

```text
08:55 ~ 09:30  유니버스 워밍업 및 세션 계획 확정
09:30 ~ 14:00  신규 매수 가능
14:00 ~ 15:00  신규 매수 중단, 보유/주문만 관리
15:00          미체결 정리 + 강제청산
15:15          브로커 체결 일괄 대조
15:20          종료
```

관련 설정은 [settings.yaml](/abs/path/c:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)에 있다.

예전 문서에는 `13:00` 기준이 남아 있었지만, 현재 기준은 `14:00`이다.

## 3. 핵심 파일

```text
Daily_bot/
  main.py                           메인 루프, 장중 흐름, 15:15 대조
  broker/kiwoom_client.py           키움 REST 래퍼, 체결조회/계좌조회
  storage/db.py                     SQLite 원장, fills/orders/market_traces 저장
  storage/audit_csv.py              감사용 CSV 누적/재생성
  risk/force_sell.py                15:00 이후 강제청산
  risk/stop_loss.py                 손절 감시/실행
  strategy/signal.py                후보 랭킹 및 최종 필터
  strategy/universe.py              유니버스 구성과 필터
  telemetry/trace_helpers.py        scan/watchlist/active_position 추적
  backtest/replay_market_traces.py  market_traces 기반 리플레이 엔진
  backtest/sweep_replay_configs.py  리플레이 설정 스윕
```

## 4. 절대 보존해야 할 동작

다음은 수정 시 깨지면 안 되는 핵심 동작입니다.

1. 매수 체결 전에는 매도 주문을 넣지 않는다.
2. 매수 체결 후에는 목표 지정가 매도 주문을 즉시 넣는다.
3. `15:00` 이후에는 신규 매수를 하지 않는다.
4. `15:15` 이후에는 브로커 체결 전체를 다시 조회해 로컬 체결 원장을 정정한다.
5. `trade_fills_audit.csv`는 append-only처럼 보이더라도, 마감 대조 후에는 DB 기준으로 재생성할 수 있어야 한다.
6. 장중 손익은 MTS가 더 신뢰도가 높고, 마감 후 기록은 브로커 대조 후 로컬 원장이 더 신뢰도가 높다.

## 5. 현재 매수 로직 해석

실거래 로직은 `selected 전용 후보`만 사는 구조가 아니다.

현재 실제 흐름은:

1. 유니버스를 스캔한다.
2. 20호가 기반으로 기대수익률을 계산한다.
3. 전체 후보를 순위화한다.
4. `top_ratio`를 적용한다.
5. 최종 필터를 적용한다.
6. 보유 종목/미체결 주문과 겹치는 종목을 제외한다.
7. 슬롯 수와 가용현금 기준으로 매수 대상을 고른다.
8. 매수 후 즉시 매도 주문을 연결한다.

따라서 전략 튜닝은 `selected`보다 `unselected` 리플레이 결과를 우선해서 보는 편이 현재 구조와 더 잘 맞는다.

## 6. 현재 기준 설정값

현재 기준선:

- `market.stop_buy_time = 14:00`
- `universe.min_market_cap_krw = 250000000000`
- `universe.min_trading_value_krw = 3000000000`
- `trend_filter.enabled = false`
- `strategy.top_ratio = 0.3`
- `strategy.min_expected_return_percent = 0.3`
- `strategy.max_spread_percent = 0.5`
- `strategy.max_prev_day_change_percent = 0.0`
- `risk.min_slot_count = 5`
- `risk.max_slot_count = 10`
- `risk.slot_budget_unit_krw = 5000000`
- `risk.max_budget_per_stock_krw = 5000000`
- `risk.stop_loss_percent = 3.0`

코드와 문서가 어긋나면 설정 파일을 우선으로 보고 문서를 갱신하는 편이 안전하다.

## 7. 체결기록 구조

### 장중

- 주문별 체결 확인은 `kiwoom_client.get_order_fill()`을 통해 수행한다.
- 이 함수는 `ka10076` 당일 체결목록을 조회한 뒤 로컬에서 주문번호를 매칭한다.
- `ord_no`를 직접 필터값처럼 쓰지 않는 이유:
  `ka10076.ord_no`는 정확한 주문번호 조회 필드가 아니라 과거 조회 커서 성격이기 때문이다.

### 장 마감 후

`main.reconcile_broker_fills()`가 아래 작업을 수행한다.

1. 브로커 체결 전체 조회
2. 주문번호/매수-매도 기준으로 `fills` 원장 정정
3. `fills_YYYYMMDD.csv` 재생성
4. `trade_fills_audit.csv` 재생성

최종 숫자는 장중 콘솔 로그보다 브로커 대조 후 DB 상태를 더 신뢰하는 편이 맞다.

## 8. 추적 기록과 백테스트

`market_traces`는 지금 단순 시세 로그보다 훨씬 중요하다.

현재 추가로 남기는 값:

- `market_cap`
- `trading_value`
- `kospi_change_percent`
- `scan_cycle_at`
- `phase=active_position`

이 값들이 있어야 나중에:

- 유니버스 필터가 후보군을 얼마나 잘라냈는지
- 손절선이 너무 타이트한지
- 보유 중 흔들림이 어느 정도였는지

를 복원해서 볼 수 있다.

또한 백테스트 CSV는 이제 `logs`가 아니라 `Daily_bot/backtest/results`에 저장한다.  
`logs`는 실거래 전용으로 유지한다.

## 9. 수정할 때의 기본 태도

- 전략 잠재수익률을 너무 보수적으로 깎아보지 말 것
- 대신 기록과 회계 숫자는 항상 의심하고 교차검증할 것
- "기록이 맞는지"와 "전략이 좋은지"를 같은 문제로 섞지 말 것

실무적으로는 아래 순서가 안전하다.

1. 브로커 원본 확인
2. DB `fills` 확인
3. CSV 재생성 여부 확인
4. 그 다음에 전략/성과 해석
