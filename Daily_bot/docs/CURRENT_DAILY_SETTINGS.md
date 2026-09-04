# Current Daily Bot Settings

이 문서는 **현재 활성 설정만 빠르게 확인하기 위한 스냅샷**이다. 값이 충돌하면 `Daily_bot/config/settings.yaml`과 실코드를 우선한다.

## Market

```yaml
prewarm_start_time: "08:55"
startup_clear_time: "09:10"
start_buy_time: "09:30"
stop_buy_time: "11:30"
force_sell_time: "15:15"
reconcile_time: "15:20"
end_time: "15:25"
```

운영 의미:

- 08:55부터 유니버스/전일 기준가/세션 자본을 준비한다.
- 09:10에는 전일 이월 포지션·주문을 먼저 정리한다.
- 신규매수는 09:30~11:30에만 허용한다.
- 15:15에는 남은 포지션을 강제청산한다.
- 15:20에는 브로커 체결내역과 로컬 기록을 재정합한다.

## Universe

```yaml
source:
  - KOSPI
  - KOSDAQ
csv_path: ""
cache_path: data/krx_latest.csv
refresh_daily: true
min_market_cap_krw: 250000000000
min_trading_value_krw: 3000000000
```

즉 당일 KOSPI+KOSDAQ 전체에서 시가총액 2,500억 원 이상, 거래대금 30억 원 이상 종목만 기본 유니버스로 사용한다.

## Strategy

```yaml
top_ratio: 1.0
max_buy_count: 3
allow_refill_empty_slots: true
min_expected_return_percent: 0.71
refill_min_expected_return_percent: 0.90
min_expected_return_fallback_percents: []
max_spread_percent: 0.0
spread_expected_return_multiplier: 0.0
min_prev_day_change_percent: 0.0
max_prev_day_change_percent: 0.0
max_intraday_jump_from_prev_scan_percent: 0.0
orderbook_bid_linear_decay_min_weight: 0.0
orderbook_ask_linear_decay_min_weight: 0.0
sell_tick_offset: 1
scan_interval_seconds: 60
```

### 실제 의미

- `top_ratio = 1.0`: 기대수익률 랭킹 비율 컷을 사실상 사용하지 않는다.
- 최초/미사용 슬롯: 기대수익률 `0.71%` 이상.
- 익절로 반환된 슬롯: 기대수익률 `0.90%` 이상.
- fallback: 비활성.
- 스프레드 필터: 비활성.
- 전일 등락률 필터: 비활성.
- 직전 스캔 급등 필터: 비활성.
- 매도호가 깊이 제한: 비활성.
- 한 번의 스캔에서 신규매수 최대 3종목.
- 익절 슬롯은 11:30 전까지 반복 재사용 가능.
- full-batch lock 없음.

## Orderbook Model

```yaml
orderbook_bid_linear_decay_min_weight: 0.0
orderbook_ask_linear_decay_min_weight: 0.0
```

매수/매도 양쪽 모두 가까운 호가를 `1.0`, 가장 먼 호가를 `0.0`으로 보고 선형 감쇠한다. 즉 현재가에서 멀어질수록 호가잔량의 영향력이 작아진다.

감쇠는 단순 후처리 필터가 아니라 `expect_price` 계산 자체에 들어간다.

## Risk / Slot Plan

```yaml
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
stop_loss_percent: 0.0
daily_loss_limit_percent: 10.0
```

### 슬롯 수

세션 시작 시 주문가능 자본을 기준으로 슬롯 수를 계산한다.

- 최소 3슬롯
- `500만 원` 단위로 증가 가능
- 최대 10슬롯
- `max_position_count = 10`이 최종 하드 상한

따라서 현재 전략은 고정 3종목 전략이 아니다.

### 손절

고정 퍼센트/틱 손절은 비활성이다.

Dynamic expected-return stop은 `.env`를 사용한다.

```text
DYNAMIC_EXPECT_STOP_PERCENT=-0.1      # 코드 기본값
DYNAMIC_EXPECT_STOP_CONSECUTIVE=3     # 코드 기본값
```

기본 동작:

- 보유 중에도 20호가로 기대수익률을 다시 계산한다.
- 기대수익률이 `-0.1% 이하`인 상태가 `3회 연속`이면 dynamic stop을 실행한다.
- 해당 종목은 같은 날 재진입 금지.
- 해당 손절이 사용하던 슬롯은 그 거래일 동안 폐쇄.
- 다음 거래일에는 슬롯 폐쇄 상태를 초기화하고 새 자본으로 슬롯 수를 다시 계산.

`DYNAMIC_EXPECT_STOP_PERCENT=off` 등으로 dynamic stop 자체를 비활성화할 수 있다.

## Slot Lifecycle

현재 슬롯 정책은 다음과 같다.

1. 세션 시작 시 자본 기준 총 슬롯 수를 정한다.
2. 아직 사용하지 않은 슬롯은 `0.71%` 기준으로 채운다.
3. 익절로 빈 슬롯은 `0.90%` 기준으로 11:30 전까지 재사용할 수 있다.
4. full-batch lock은 없다.
5. dynamic stop/손절 슬롯은 그날만 폐쇄한다.
6. 다음 거래일에는 전체 슬롯 계획을 다시 계산한다.

실거래에서는 `session_slot_runner.py`가 이 정책을 `main.py` 위에 설치한다.

## Backtest Alignment

현재 전략과 맞춘 비교 러너는 `replay_refill_threshold.py`다.

```bash
python -m Daily_bot.backtest.replay_refill_threshold \
  --refill-min-expected-return 0.90 \
  --stop-buy-time 11:30 \
  --logs-dir Daily_bot/logs
```

live-equivalent 슬롯 폐쇄 기본 로직은 `replay_dynamic_expect_debounce_reserve_stopped_slots.py`에 있다.

현재 정합성 기준:

- 최초 진입 `0.71%`
- 익절 재진입 `0.90%`
- `top_ratio = 1.0`
- 전일 등락률 필터 OFF
- dynamic stop `-0.1% / 3회`
- 손절 슬롯 당일 폐쇄
- 손절 종목 당일 재진입 금지
- 11:30 신규매수 마감
- 자본 기반 슬롯 계산

## Runtime Entry

권장 실거래 실행:

```powershell
.\Daily_bot\scripts\run_real.ps1
```

이 스크립트는 `Daily_bot/session_slot_runner.py --real`을 실행한다.
