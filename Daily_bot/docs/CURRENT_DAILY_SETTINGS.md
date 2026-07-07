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
- `strategy.min_expected_return_percent = 0.7`
- `strategy.min_expected_return_fallback_percents = [0.6, 0.5]`
- `strategy.max_spread_percent = 0.0`
- `strategy.spread_expected_return_multiplier = 0.0`
- `strategy.min_prev_day_change_percent = 0.0`
- `strategy.max_prev_day_change_percent = 0.0`
- `strategy.max_intraday_jump_from_prev_scan_percent = 0.0`
- `strategy.sell_tick_offset = 1`
- `strategy.scan_interval_seconds = 60`

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
- 기본 실거래 동작은 `빈 슬롯이 생겨도 전체 배치가 완전히 비기 전까지 재진입하지 않음` 이다.
- fallback 은 배치가 완전히 비어 있고 기본 기대수익률 문턱에서 후보가 0개일 때만 `0.6 -> 0.5` 순서로 재평가한다.
- 현재 스프레드 필터, 직전 스캔 급등 필터, 매도호가 잔량 비율 필터는 모두 꺼져 있다.

## Backtest Defaults

- `--min-expected-return` 기본값은 config 의 `strategy.min_expected_return_percent`
- `--fallback-min-expected-returns` 기본값은 config 의 `strategy.min_expected_return_fallback_percents`
- `--start-buy-time`, `--stop-buy-time`, `--force-sell-time` 기본값은 config 의 `market` 값
- `--stop-loss`, `--stop-loss-tick-count`, `--stop-loss-tick-multiplier` 기본값은 config 의 `risk` 값
- `--min-slot-count`, `--max-slot-count`, `--slot-budget-unit-krw`, `--max-budget-per-stock-krw`, `--max-position-count`, `--target-budget-ratio-per-stock` 기본값은 config 와 정렬된다
- `--starting-capital-krw` 기본값은 `1000000`
- `--use-selected-signals` 기본값은 `false`
- `--disallow-refill-empty-slots` 가 기본 동작이다

## Replay Fidelity Notes

- 리플레이는 `scan_cycle_at` 기준으로 한 스캔 배치를 묶어서 엔트리를 판단한다.
- 신규 진입 후보는 `scan_candidate` 행만 사용한다.
- 리플레이도 라이브와 같이 fallback, 직전 스캔 급등 필터, `select_affordable_targets` 조합 선택을 따른다.
- 그래도 부분체결, 60초 사이의 순간 고가/저가, 주문 취소-재주문, 브로커 체결 순서까지 완전히 같지는 않다.
