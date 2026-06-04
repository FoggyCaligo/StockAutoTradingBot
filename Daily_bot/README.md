# Daily Bot

키움 REST API를 이용해 KOSPI200 종목을 장중 스캔하고, 호가 기반 예상가로 단기 매매를 수행하는 데일리 자동매매 봇입니다.

## 시간대별 작동 구조

### 1. 유니버스 워밍업: 08:55 ~ 09:30

- `prewarm_start_time`: `08:55`
- `start_buy_time`: `09:30`
- 이 구간에서는 실제 매수는 하지 않고, 당일 스캔에 사용할 유니버스와 후보 계산 준비만 수행합니다.

이 시간대에 준비하는 종목 선정 기준은 아래와 같습니다.

#### 유니버스 기준

- 기본 대상: `KOSPI200`
- 시가총액 하한: `300,000,000,000원` 이상
- 거래대금 하한: `5,000,000,000원` 이상
- 데이터 소스: 당일 갱신 가능한 경우 `FinanceDataReader`, 실패 시 로컬 캐시/CSV fallback

#### 추세 필터 기준

- `trend_filter.enabled: true`
- 최근 일봉 기준으로
  - `5일 이동평균선 기울기 > 0` 또는
  - `20일 이동평균선 기울기 > 0`
- 계산에 필요한 데이터가 너무 짧으면 제외됩니다.

#### 호가 예측 알고리즘 작동 원리

- 각 종목의 20호가 스냅샷을 조회합니다.
- 매수호가 잔량과 매도호가 잔량을 `1:1`로 차감해가며 상쇄합니다.
- 한쪽 호가가 먼저 소진되면 그 시점의 bid/ask frontier를 기준으로 가격대를 잡습니다.
- 최종 예상가는 그 frontier의 중간값을 호가단위에 맞게 절사한 값입니다.
- 목표 매도가는 `예상가 - 1틱`으로 계산합니다.

#### 기대수익률 계산 기준

- 현재가: 호가 응답의 `current_price`
- 예상가: 위 호가 예측 알고리즘 결과
- 목표 매도가: `예상가 - 1틱`
- 기대수익률:

```text
(목표 매도가 - 현재가) / 현재가 * 100
```

#### 최종 후보 필터 기준

- 기대수익률 하한: `0.25%` 이상
- 스프레드 상한: `0.7%` 이하
- 목표 매도가(`예상가 - 1틱`)가 현재가보다 높아야 함
- 이미 보유 중이거나 미체결 주문이 걸린 종목은 제외

#### 정렬과 최종 매수 대상 선정 방식

- 전체 후보를 기대수익률 내림차순으로 정렬합니다.
- 그중 상위 `20%`(`top_ratio: 0.20`)만 1차 후보로 남깁니다.
- 이후 현재 주문가능현금과 슬롯 수를 기준으로 실제 매수 가능한 조합만 다시 고릅니다.
- 목표 종목 수는 `min_slot_count`, `target_budget_ratio_per_stock`, `max_budget_per_stock_krw`를 기준으로 자금에 따라 유동적으로 줄고 늘어납니다.
- `max_buy_count: 0`, `max_position_count: 0`이면 고정 상한 없이 자금 규모에 따라 종목 수가 계속 늘어납니다.
- 비싼 종목 때문에 슬롯이 비면, 더 아래의 저렴한 후보로 내려가며 채웁니다.

### 2. 실매매 시간대: 09:30 ~ 13:00

- `stop_buy_time`: `13:00`
- 이 구간이 실제 매수/매도 관리가 이루어지는 핵심 시간대입니다.
- 루프는 `scan_interval_seconds: 60` 기준으로 반복됩니다.

#### 매수 조건

- KOSPI200 유니버스 기준 통과
- 시가총액 `3000억 이상`
- 거래대금 `50억 이상`
- 추세 필터 통과
- 기대수익률 `0.25% 이상`
- 스프레드 `0.7% 이하`
- 목표 매도가가 현재가보다 높음
- 현재 보유/미체결과 중복되지 않음
- 일일 손실률 제한에 걸리지 않음
- 남은 포지션 슬롯이 있음
- 현재 현금으로 실제 1주 이상 매수 가능

#### 매수 방식

- 종목별 지정가 매수
- 최소 `3슬롯`을 기본으로 깔고, 주문가능현금의 약 `33%`를 종목당 목표 예산으로 삼아 종목 수를 계산
- 종목당 목표 예산은 최대 `500만원`으로 제한
- 실제 주문 때는 주문가능현금을 기준으로 선택된 종목 수에 맞춰 예산을 분배
- 현재는 고정 종목 수 상한 없이, 자금과 후보 수에 맞춰 진입

#### 매도 조건

- 매수 체결 직후: 목표 지정가 매도 주문
- 목표 매도가 기준: `예상가 - 1틱`
- 장중 손절 조건 충족 시: 시장가 매도
- 강제 청산 시간 도달 시: 미체결 취소 후 시장가 전량 매도

#### 안전장치

현재 주요 안전장치는 아래 `6개`입니다.

1. `max_position_count`로 동시 보유 종목 수 제한
2. `daily_loss_limit_percent`로 일일 손실률 초과 시 신규 매수 차단
3. `stop_loss_percent`로 보유 종목 손절
4. 매수 미체결 시 취소 후 부분체결 복구 로직 수행
5. 주문 상태 조회 실패 시 실제 보유 수량 기준 목표 매도 복구
6. `force_sell_time` 이후 미체결 정리 후 전량 강제 청산

### 3. 13:00 ~ 15:00: 신규 매수 중단, 보유/주문 관리만 유지

- `13:00` 이후에는 신규 매수를 더 하지 않습니다.
- 다만 이미 보유 중인 종목의 손절 감시와 기존 주문 상태 관리는 계속 수행합니다.

### 4. 15:00 이후: 강제 청산 후 종료

- `force_sell_time`: `15:00`
- 남아 있는 미체결 주문을 취소합니다.
- 남은 보유 종목을 시장가로 전량 매도합니다.
- 체결 내역을 기록한 뒤 당일 세션을 종료합니다.

## 현재 기본 설정값

```yaml
market:
  prewarm_start_time: "08:55"
  start_buy_time: "09:30"
  stop_buy_time: "13:00"
  force_sell_time: "15:00"

strategy:
  top_ratio: 0.20
  max_buy_count: 0
  min_expected_return_percent: 0.25
  max_spread_percent: 0.7
  sell_tick_offset: 1
  scan_interval_seconds: 60

risk:
  max_position_count: 0
  min_slot_count: 3
  target_budget_ratio_per_stock: 0.33
  max_budget_per_stock_krw: 5000000
  stop_loss_percent: 5.0
  daily_loss_limit_percent: 10.0
```

## 실행

### 실거래

```bash
python main.py --real
```

### 드라이런

```bash
python main.py --dry-run
```

## 백테스트 / 리플레이

`Daily_bot`은 현재 두 가지 방식으로 과거 데이터를 다시 볼 수 있습니다.

### 1. market_traces 리플레이

파일:

```text
Daily_bot/backtest/replay_market_traces.py
```

용도:

- 장중에 기록된 `market_traces`를 바탕으로
- 어떤 종목에 진입했을지
- `take_profit`, `stop_loss` 기준으로 결과가 어땠을지를
- 가볍게 재생하는 리플레이형 백테스트입니다.

기본적으로는 실제로 `selected=1`로 기록된 종목이 있으면 그 종목을 우선 사용합니다.

예시:

```bash
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py ^
  --db .\Daily_bot\bot.sqlite3 ^
  --min-expected-return 0.25 ^
  --max-spread 0.7 ^
  --top-n 3 ^
  --take-profit 0.25 ^
  --stop-loss 6.0 ^
  --out .\Daily_bot\logs\backtest_replay.csv
```

실제 선택 신호를 무시하고, 순수 필터/랭킹 기준으로만 다시 고르고 싶으면:

```bash
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py ^
  --db .\Daily_bot\bot.sqlite3 ^
  --ignore-selected-signals
```

### 2. 설정값 스윕 비교

파일:

```text
Daily_bot/backtest/sweep_replay_configs.py
```

용도:

아래 설정값을 여러 조합으로 바꿔가며 리플레이 결과를 비교합니다.

- `min_expected_return_percent`
- `max_spread_percent`
- `top_n_per_day` (`max_buy_count`에 대응)
- `stop_loss_percent`

출력:

- CSV 요약 파일
- 각 조합별 거래 수 / 승률 / 평균 손익률 / 누적 손익률

예시:

```bash
.\.venv\Scripts\python.exe .\Daily_bot\backtest\sweep_replay_configs.py ^
  --db .\Daily_bot\bot.sqlite3 ^
  --min-expected-returns 0.2,0.25,0.3 ^
  --max-spreads 0.5,0.7 ^
  --top-ns 1,2,3 ^
  --take-profit 0.25 ^
  --stop-losses 5.0,6.0,7.0 ^
  --out .\Daily_bot\logs\backtest_replay_sweep.csv
```

실제 선택 신호를 무시하고 필터/랭킹만으로 비교하고 싶으면:

```bash
.\.venv\Scripts\python.exe .\Daily_bot\backtest\sweep_replay_configs.py ^
  --db .\Daily_bot\bot.sqlite3 ^
  --ignore-selected-signals
```

## 빠른 점검 포인트

### 봇이 살아 있는지 확인

- `Daily_bot/logs/run_real.lock`의 PID와 시작 시각 확인

### 스캔/주문이 실제로 돌고 있는지 확인

- `Daily_bot/bot.sqlite3`
- `hoga_snapshots`
- `signals`
- `orders`
- `fills`
- `market_traces`
- `account_traces`

이 테이블들의 건수가 증가하면 실제 로직이 진행 중인 것입니다.

### 계좌 상태 확인

- 보유 종목
- 미체결 주문
- 주문가능현금

이 세 가지를 함께 봐야

- 아직 시작 전인지
- 슬롯이 찬 상태인지
- 후보는 있었지만 자금 때문에 일부만 매수된 건지

를 구분하기 쉽습니다.

## 테스트

```bash
.\.venv\Scripts\python.exe -m pytest Daily_bot\tests
```
