# Current Daily Bot Settings

이 문서는 현재 [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml) 과 [replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py) 기준의 기본 운영값 요약이다. 개념 설명은 [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md) 를 본다.

## Universe

- `universe.source = KOSPI`
- `universe.csv_path = ""`
- `universe.cache_path = data/kospi_latest.csv`
- `universe.refresh_daily = true`
- `universe.min_market_cap_krw = 250000000000`
- `universe.min_trading_value_krw = 3000000000`

## Strategy

- `strategy.top_ratio = 1.0`
- `strategy.max_buy_count = 3`
- `strategy.allow_refill_empty_slots = true`
- `strategy.min_expected_return_percent = 0.7`
- `strategy.min_expected_return_fallback_percents = [0.6, 0.5]`
- `strategy.max_spread_percent = 0.0`
- `strategy.spread_expected_return_multiplier = 0.0`
- `strategy.min_prev_day_change_percent = 0.0`
- `strategy.max_prev_day_change_percent = 0.0`
- `strategy.max_intraday_jump_from_prev_scan_percent = 0.0`
- `strategy.sell_tick_offset = 1`
- `strategy.scan_interval_seconds = 60`

## Strategy Direction

최근 백테스트 결과, 이동평균/스프레드/전일 등락률/직전 스캔 급등/호가잔량비율 같은 보조 필터는 호가 기반 기대수익률 알고리즘이 포착한 유효 기회를 과도하게 제거하는 경향이 있었다.

따라서 현재 Daily_bot은 보조 필터를 최소화하고, 20호가 기반 기대수익률이 `0.7%` 이상으로 계산되는 순간을 적극적으로 포착하는 방향으로 운영한다. 품질 관리는 추가 필터를 많이 겹치는 방식이 아니라 기대수익률 기준을 높이는 방식으로 수행한다.

`0.7%` 이상의 기회는 장중 특정 순간에 짧게 발생했다가 사라질 수 있으므로, 빈 슬롯이 생기면 재진입을 허용한다. 이 방식은 후보 수 부족 문제를 줄이고, 장중 반복적으로 발생하는 호가 불균형 시점을 더 잘 잡기 위한 것이다.

위험 관리는 진입 전 보조 필터가 아니라 손절, 강제매도, 일손실 제한, 체결 기록, 체결 복구 로직으로 처리한다. 즉, 현재 구조는 보수적인 필터형 봇이 아니라 호가 예측 신호 극대화형 단기 회전 봇에 가깝다.

## Market Times

- `market.prewarm_start_time = 08:55`
- `market.start_buy_time = 09:30`
- `market.stop_buy_time = 11:30`
- `market.force_sell_time = 15:00`
- `market.reconcile_time = 15:15`
- `market.end_time = 15:20`

## Risk

- `risk.max_position_count = 10`
- `risk.min_slot_count = 3`
- `risk.slot_budget_unit_krw = 5000000`
- `risk.max_slot_count = 10`
- `risk.target_budget_ratio_per_stock = 0.50`
- `risk.max_budget_per_cycle_krw = 0`
- `risk.max_budget_per_stock_krw = 0`
- `risk.max_orderbook_ask_depth_ratio = 0.0`
- `risk.stop_loss_tick_count = 0`
- `risk.stop_loss_tick_multiplier = 0.0`
- `risk.stop_loss_percent = 4.5`
- `risk.daily_loss_limit_percent = 10.0`

## Important Behavior Notes

- `max_buy_count = 3` 는 한 번의 신규 진입 배치 상한이다.
- 실제 총 보유 가능 종목 수 상한은 `risk.max_position_count = 10` 과 자본 기반 슬롯 계산으로 정해진다.
- 기본 실거래 동작은 `빈 슬롯이 생기면 재진입을 허용함` 이다.
- fallback 은 기본 기대수익률 문턱에서 후보가 부족할 때만 `0.6 -> 0.5` 순서로 재평가한다.
- 현재 스프레드 필터, 직전 스캔 급등 필터, 매도호가 잔량 비율 필터는 모두 꺼져 있다.

## Backtest Defaults

- `--min-expected-return` 기본값은 config 의 `strategy.min_expected_return_percent`
- `--fallback-min-expected-returns` 기본값은 config 의 `strategy.min_expected_return_fallback_percents`
- `--start-buy-time`, `--stop-buy-time`, `--force-sell-time` 기본값은 config 의 `market` 값
- `--stop-loss`, `--stop-loss-tick-count`, `--stop-loss-tick-multiplier` 기본값은 config 의 `risk` 값
- `--min-slot-count`, `--max-slot-count`, `--slot-budget-unit-krw`, `--max-budget-per-stock-krw`, `--max-position-count`, `--target-budget-ratio-per-stock` 기본값은 config 와 정렬된다
- `--starting-capital-krw` 기본값은 `1000000`
- `--use-selected-signals` 기본값은 `false`
- 기본 리플레이 동작은 빈 슬롯 재진입을 허용하며, `--disallow-refill-empty-slots` 를 지정하면 재진입을 막는다.

## Replay Fidelity Notes

- 리플레이는 `scan_cycle_at` 기준으로 한 스캔 배치를 묶어서 엔트리를 판단한다.
- 신규 진입 후보는 `scan_candidate` 행만 사용한다.
- 리플레이도 라이브와 같이 fallback, 직전 스캔 급등 필터, `select_affordable_targets` 조합 선택을 따른다.
- 그래도 부분체결, 60초 사이의 순간 고가/저가, 주문 취소-재주문, 브로커 체결 순서까지 완전히 같지는 않다.
