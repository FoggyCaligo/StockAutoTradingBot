# Daily Bot Logic Reference

이 문서는 **현재 실거래 전략과 백테스트가 어떻게 맞물려 동작하는지 설명하는 기준 문서**다.

최종 진실원천 우선순위:

1. `Daily_bot/config/settings.yaml`
2. `Daily_bot/session_slot_runner.py`
3. `Daily_bot/main.py`
4. `Daily_bot/risk/stop_loss.py`
5. `Daily_bot/backtest/replay_refill_threshold.py`
6. `Daily_bot/backtest/replay_dynamic_expect_debounce_reserve_stopped_slots.py`

`Diary.md`와 `memo.txt`는 과거 실험 기록이므로 현재 활성 전략과 구분한다.

---

## 1. 전략 목적

Daily Bot은 장중 20호가에서 **단기 균형가격 `expect_price`**를 추정하고, 현재가 대비 충분한 기대수익이 있을 때 짧게 진입하는 전략이다.

핵심 흐름:

```text
당일 유니버스 구성
→ 20호가 수집
→ 거리 가중치 적용
→ expect_price 계산
→ 기대수익률 계산
→ 슬롯/현금/진입 기준 확인
→ 매수
→ 즉시 목표 지정가 매도
→ 보유 중 예상가 재계산
→ 익절 / dynamic stop / 장마감 강제청산
```

---

## 2. 유니버스

현재 유니버스는 KOSPI와 KOSDAQ 전체를 당일 갱신해 만든다.

기본 조건:

- 시장: `KOSPI`, `KOSDAQ`
- 최소 시가총액: `250,000,000,000 KRW` (2,500억 원)
- 최소 거래대금: `3,000,000,000 KRW` (30억 원)
- CSV 고정 유니버스: 사용하지 않음
- 일일 새로고침: 사용
- 추세 필터: OFF

즉 시장 전체를 넓게 보되 거래가 지나치게 얇은 종목만 먼저 제거한다.

---

## 3. 시간대 운영

현재 시간 설정:

```text
08:55  prewarm 시작
09:10  전일 이월 포지션/주문 정리
09:30  신규매수 시작
11:30  신규매수 종료
15:15  잔여 포지션 강제청산
15:20  브로커 체결 재정합
15:25  세션 종료
```

### 08:55 prewarm

- 유니버스 준비
- 전일 기준가 기록
- 주문가능 자본 확인
- 세션 슬롯 수 계산
- 슬롯당 계획 예산 계산

### 09:10 carryover clear

전일 포지션이나 미체결 주문이 남아 있으면 신규매매보다 먼저 정리한다.

### 09:30~11:30 buy window

신규 진입과 익절 슬롯 재진입은 이 시간 안에서만 가능하다.

### 15:15 force sell

오버나이트 포지션으로 넘어가지 않도록 남은 포지션을 정리한다.

---

## 4. 스캔 단위

기본 스캔 간격은 60초다.

각 스캔에서:

1. 유니버스 종목을 순회한다.
2. 각 종목의 20호가를 조회한다.
3. 호가 감쇠를 적용한다.
4. 예상가와 기대수익률을 계산한다.
5. 필터를 통과한 후보를 만든다.
6. 계좌의 활성 포지션/주문과 빈 슬롯을 다시 확인한다.
7. 살 수 있는 후보만 실제 매수 대상으로 고른다.

`max_buy_count = 3`은 한 스캔에서 새로 살 수 있는 종목 수 상한이다.

---

## 5. 호가 가중치와 예상가

현재 전략은 먼 호가잔량을 가까운 잔량과 동일하게 보지 않는다.

매수/매도 양쪽 모두:

- 가장 가까운 호가: 가중치 `1.0`
- 가장 먼 호가: 가중치 `0.0`
- 중간 호가: 거리 순으로 선형 감소

현재 설정:

```yaml
orderbook_bid_linear_decay_min_weight: 0.0
orderbook_ask_linear_decay_min_weight: 0.0
```

따라서 현재가/중간값에서 멀수록 호가잔량의 영향력이 작아진다.

중요: 이 감쇠는 후보를 걸러내는 후처리 필터가 아니다. **감쇠된 잔량으로 `expect_price` 자체를 다시 계산한다.**

---

## 6. 목표 매도가와 기대수익률

호가 상쇄 모델이 계산한 `expect_price`를 그대로 목표가로 쓰지 않고 `sell_tick_offset = 1`틱을 낮춘 가격을 목표 매도가로 사용한다.

개념적으로:

```text
target_sell_price = expect_price - 1 tick
expected_return = (target_sell_price - current_price) / current_price * 100
```

이 `expected_return`이 진입 신호의 핵심 값이다.

---

## 7. 현재 활성 진입 필터

### 최초 진입 / 미사용 슬롯

```text
expected_return >= 0.71%
```

### 익절 반환 슬롯 재진입

```text
expected_return >= 0.90%
```

### 현재 비활성 필터

- `top_ratio = 1.0`: 비율 랭킹 컷 없음
- fallback: OFF
- 스프레드 상한: OFF
- 전일 등락률 범위: OFF
- 직전 스캔 급등 필터: OFF
- 매도호가 깊이 제한: OFF
- 추세 필터: OFF

즉 현재 전략은 필터를 많이 겹치기보다 기대수익률 신호와 슬롯 정책을 중심으로 운용한다.

---

## 8. 자본 기반 슬롯 계산

슬롯 수는 고정 3개가 아니다.

현재 설정:

```yaml
min_slot_count: 3
slot_budget_unit_krw: 5000000
max_slot_count: 10
max_position_count: 10
```

세션 시작 시 주문가능 자본을 기준으로 총 슬롯 수를 계산한다.

예시 개념:

```text
자본이 작음 → 최소 3슬롯
자본 증가 → 500만원 단위로 슬롯 증가 가능
상한 → 10슬롯
```

실제 포지션 한도는 계산된 슬롯 수와 `max_position_count` 중 더 낮은 제한을 따른다.

슬롯 수는 세션 중 임의로 계속 늘리지 않고 세션 시작 계획을 기준으로 운용한다.

---

## 9. 슬롯 상태의 종류

실거래에서는 `session_slot_runner.py`가 `main.py` 위에 슬롯 정책을 설치한다.

### 9.1 미사용 슬롯

아직 세션에서 한 번도 사용하지 않은 슬롯이다.

- 진입 기준: `0.71%`

### 9.2 익절 반환 슬롯

한 번 사용한 뒤 `take_profit`으로 비워진 슬롯이다.

- 11:30 이전이면 재사용 가능
- 재진입 기준: `0.90%`
- 여러 번 익절/재사용 가능

### 9.3 손절 폐쇄 슬롯

Dynamic stop 또는 손절이 발생한 슬롯이다.

- 해당 거래일 동안 사용 불가
- 세션 유효 포지션 한도를 1개 줄이는 효과
- 다음 거래일에는 폐쇄 상태 초기화

현재 **full-batch lock은 사용하지 않는다.** 전체 슬롯이 한 번 꽉 차도 익절로 빈 슬롯이 생기면 다시 사용할 수 있다.

---

## 10. 동일 종목 재진입 제한

손절된 종목은 그 거래일 동안 `blocked_stop_loss_tickers`에 들어가 같은 날 재진입하지 않는다.

따라서 dynamic stop 후에는:

1. 해당 종목 자체 재진입 금지
2. 해당 슬롯도 그날 폐쇄

두 제한이 동시에 적용된다.

---

## 11. 정상 진입 후 매도

매수가 체결되면 목표 지정가 매도를 즉시 제출한다.

정상 성공 흐름:

```text
BUY fill
→ 목표 SELL limit 주문
→ 목표가 체결
→ take_profit
→ 슬롯 반환
```

익절된 슬롯은 11:30 전이라면 0.90% 기준으로 다시 사용할 수 있다.

---

## 12. Dynamic Expected-Return Stop

현재 고정 손절 설정은 모두 0으로 비활성이다.

```yaml
stop_loss_tick_count: 0
stop_loss_tick_multiplier: 0.0
stop_loss_percent: 0.0
```

대신 보유 중인 포지션도 현재 20호가로 기대수익률을 다시 계산한다.

코드 기본값:

```text
DYNAMIC_EXPECT_STOP_PERCENT = -0.1
DYNAMIC_EXPECT_STOP_CONSECUTIVE = 3
```

즉:

```text
expected_return <= -0.1%
```

상태가 3회 연속 관측되면 dynamic stop이 발생한다.

한 번 임계값 위로 회복되면 연속 카운트는 다시 0으로 리셋된다.

### Dynamic stop 실행 과정

1. 기존 해당 종목 매도주문 취소
2. 미체결 주문이 없어졌는지 확인
3. best bid 중심의 실행 가능한 가격 확인
4. 보유수량 전체 매도주문
5. 체결 기록
6. 해당 종목 same-day block
7. 해당 슬롯 same-day close

`.env`에서 임계값/연속횟수를 바꿀 수 있고 `DYNAMIC_EXPECT_STOP_PERCENT=off`로 끌 수도 있다.

---

## 13. 일손실 제한

```yaml
daily_loss_limit_percent: 10.0
```

계좌의 조정 자산가치가 시작 자산 대비 일손실 한도에 도달하면 **새로운 매수만 막는다.** 기존 포지션 관리는 계속한다.

---

## 14. 장마감 청산과 재정합

### 15:15 force sell

남은 포지션을 모두 정리한다.

### 15:20 reconciliation

브로커의 실제 체결내역을 다시 조회해 로컬 체결 기록과 맞춘다.

이 단계는 실시간 API 누락이나 추정성 reconciliation 기록 때문에 최종 손익이 왜곡되는 것을 줄이기 위한 장치다.

---

## 15. 기록 데이터

주요 로그:

- `market_traces_YYYYMMDD.csv`: 당시 후보/호가/raw 데이터
- `fills_YYYYMMDD.csv`: 체결
- `orders_YYYYMMDD.csv`: 주문
- `account_traces_YYYYMMDD.csv`: 계좌 상태
- `daily_reference_prices_YYYYMMDD.csv`: 전일 기준가
- `daily_rev.csv`: 일별 손익 요약

`market_traces.raw_json`이 백테스트 재구성에서 특히 중요하다.

---

## 16. 백테스트 구조

백테스트는 단순 OHLC 수익률 계산보다 **당시 실거래 의사결정 재구성**에 가깝다.

현재 중요 러너:

### `replay_dynamic_expect_debounce_reserve_stopped_slots.py`

다음을 재현한다.

- dynamic stop `-0.1% / 3회`
- 손절 슬롯 당일 폐쇄
- 손절 종목 당일 재진입 금지
- 자본 기반 슬롯
- 빈 슬롯 재사용
- 기본 11:30 매수 마감

### `replay_refill_threshold.py`

위 슬롯 정책에 **익절 반환 슬롯 전용 기대수익률 기준**을 추가한다.

현재 전략 비교 실행:

```bash
python -m Daily_bot.backtest.replay_refill_threshold \
  --refill-min-expected-return 0.90 \
  --stop-buy-time 11:30 \
  --logs-dir Daily_bot/logs
```

---

## 17. 백테스트와 실거래의 정합성

현재 맞추려는 요소:

- 같은 호가 감쇠 모델
- 같은 예상가 계산
- 최초 진입 0.71%
- 익절 재진입 0.90%
- dynamic stop -0.1% / 3회
- 손절 슬롯 당일 폐쇄
- 손절 종목 same-day block
- 자본 기반 슬롯 수
- 11:30 신규매수 종료
- 목표가 기반 익절
- 장마감 청산

완전히 동일하지 않을 수 있는 요소:

- 60초 스캔 사이 순간 가격 움직임
- 부분체결 타이밍
- 취소와 재주문의 브로커 내부 순서
- 실제 주문 대기열 우선순위
- API 응답 지연
- 누락된 market trace

따라서 replay 결과는 현재 전략의 구조를 검증하는 강한 근사치지만 실체결의 완전한 복제본은 아니다.

---

## 18. 현재 정책을 한 문장으로 요약

> KOSPI+KOSDAQ의 유동성 있는 종목을 20호가 기반 균형가격으로 평가하고, 미사용 슬롯은 0.71%, 익절 반환 슬롯은 0.90% 기준으로 11:30까지 반복 운용하되, 예상가가 -0.1% 이하로 3회 연속 무너지면 해당 포지션을 청산하고 그 슬롯은 당일 폐쇄한다.
