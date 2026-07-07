# Daily Bot Strategy Design

## 1. Goal

Daily Bot의 목표는 `KOSPI200` 안에서 장중 짧은 기대수익 기회를 스캔하고, 조건을 통과한 소수 종목만 당일 진입해 목표가 또는 리스크 규칙으로 정리하는 것이다.

현재 설계 철학은 이렇다.

- 유니버스는 넓지만 대형주와 거래대금으로 1차 제한한다.
- 세션 초반에 슬롯 계획을 고정해 자본 배분을 단순화한다.
- 기대수익률 중심으로 후보를 압축한다.
- 체결 직후 바로 목표가 매도 주문을 걸어 당일 청산을 기본으로 한다.
- 체결 누락과 부분체결을 로그/복구 로직으로 최대한 추적한다.

## 2. Universe

현재 유니버스:

- 소스: `KOSPI200`
- 시가총액 하한: `250,000,000,000 KRW`
- 거래대금 하한: `3,000,000,000 KRW`
- 추세 필터: `OFF`

추세 필터 코드는 남아 있지만 현재 운영값은 `false`다.

## 3. Signal Calculation

각 종목에 대해:

1. 20호가를 조회한다.
2. 호가 기반 기대가격을 계산한다.
3. `sell_tick_offset = 1`을 반영해 목표 매도가를 만든다.
4. 현재가 대비 기대수익률을 계산한다.

개념식:

```text
expect_revenue_percent = (target_sell_price - current_price) / current_price * 100
```

현재 실코드에서는 목표 매도가가 진입가보다 낮아지지 않도록 최소 1틱 위 가격을 강제한다.

## 4. Current Entry Filters

현재 최종 필터는 사실상 기대수익률 중심이다.

- 기본은 `min_expected_return_percent >= 0.7`
- 단, 배치가 완전히 비어 있고 기본 문턱에서 후보가 0개면 `min_expected_return_fallback_percents = [0.6, 0.5]` 순서로 같은 스캔을 재평가한다.
- `max_spread_percent = 0.0` 이므로 스프레드 상한 필터는 꺼져 있다.
- `min_prev_day_change_percent = 0.0`
- `max_prev_day_change_percent = 0.0`
- `max_intraday_jump_from_prev_scan_percent = 0.0`
- `max_orderbook_ask_depth_ratio = 0.0`

즉 현재는 전일등락률, 직전 스캔 점프, 호가잔량 비율 필터가 모두 비활성화돼 있고, 기대수익률과 기본 실행 가능성 조건이 핵심이다.

## 5. Capital Planning

세션 시작 시점 자본을 기준으로 슬롯 계획을 고정한다.

- 최소 슬롯 수: `3`
- 최대 슬롯 수: `10`
- 슬롯 예산 단위: `5,000,000 KRW`
- 동시 신규 진입 상한: `max_buy_count = 3`
- 보유/오더 포함 하드 상한: `max_position_count = 10`

실거래와 백테스트 모두 자본에 따라 슬롯 수와 종목당 예산을 다시 계산한다.

## 6. Entry and Exit Behavior

실거래 흐름:

1. 스캔 후 조건을 통과한 후보를 고른다.
2. 이미 보유/미체결 상태인 종목은 제외한다.
3. 자본과 슬롯 계획 안에서 살 수 있는 수량을 계산한다.
4. 매수 체결 직후 목표가 지정가 매도를 낸다.
5. 손절이 오면 기존 오더를 취소하고 손절 매도를 낸다.
6. `15:00` 이후 남은 포지션은 강제 청산한다.
7. `15:15`에 브로커 체결로 `fills`를 다시 맞춘다.

현재 손절 설정:

- `stop_loss_percent = 4.5`
- `stop_loss_tick_count = 0`
- `stop_loss_tick_multiplier = 0.0`

즉 현재는 퍼센트 손절만 켜져 있다.

## 7. Batch Wait Behavior

현재 실코드의 중요한 특징은 배치 대기다.

- 활성 포지션이나 미체결 주문이 하나라도 남아 있으면 신규 배치를 다시 시작하지 않는다.
- 일부 슬롯만 비더라도 바로 리필하지 않는다.
- 완전히 평탄화된 뒤에만 다음 스캔 배치를 다시 탄다.

이 점 때문에 `max_position_count = 10`이라도 실제 신규 진입 흐름은 더 보수적으로 보일 수 있다.

## 8. Logging and Recovery

주요 기록:

- `market_traces`
- `signals`
- `orders`
- `fills`
- `account_traces`
- `daily_reference_prices`

이 기록으로 다음을 복구하거나 분석한다.

- 선택 후보 추적
- 실제 주문/체결 복원
- 부분체결 후 복구 매도 여부 확인
- 손절/강제청산 사유 추적
- EOD 체결 보정

## 9. Replay Backtest Limits

리플레이는 실거래를 그대로 복사하는 엔진이 아니다.

- `market_traces` 기반으로 진입/청산을 단순화한다.
- 실제 부분체결, 취소 후 추가체결, 복구 주문, 분할 매도는 완전히 재현하지 못한다.
- `--use-selected-signals`를 켜지 않으면 저장된 선택 신호가 아니라 조건 통과 후보를 다시 고른다.
- 리플레이도 라이브와 같은 fallback 규칙을 따라, 비어 있는 배치에서만 낮은 기대수익률 문턱을 한 번 더 적용한다.
- `--starting-capital-krw` 기본값은 `100만원`이다.

따라서 실거래 손익과 리플레이 손익이 다를 때는 먼저 아래를 확인해야 한다.

1. `--use-selected-signals` 사용 여부
2. 시작자본 가정
3. 부분체결/분할청산 존재 여부
4. 해당 날짜 로그가 장 전체를 덮는지 여부
