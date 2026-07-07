# Current Daily Bot Settings

이 문서는 현재 `Daily_bot/config/settings.yaml`과 `Daily_bot/backtest/replay_market_traces.py` 기준의 실거래/백테스트 기본값 요약이다.

## Core Filters

- `universe.source = KOSPI`
- `universe.csv_path = ""`
- `universe.cache_path = data/kospi_latest.csv`
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

## Timing

- `market.prewarm_start_time = 08:55`
- `market.start_buy_time = 09:30`
- `market.stop_buy_time = 11:30`
- `market.force_sell_time = 15:00`
- `market.reconcile_time = 15:15`
- `market.end_time = 15:20`

## Risk

- `risk.dry_run = false`
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

## Backtest Defaults

백테스트 스크립트는 `--config`를 통해 설정 파일 기본값을 읽는다.

- `--min-expected-return`: 기본값은 config의 `strategy.min_expected_return_percent`
- `--fallback-min-expected-returns`: 기본값은 config의 `strategy.min_expected_return_fallback_percents`
- `--max-spread`: config의 `strategy.max_spread_percent`
- `--min-prev-day-change`: config의 `strategy.min_prev_day_change_percent`
- `--max-prev-day-change`: config의 `strategy.max_prev_day_change_percent`
- `--top-ratio`: config의 `strategy.top_ratio`
- `--stop-loss`: config의 `risk.stop_loss_percent`
- `--stop-loss-tick-count`: config의 `risk.stop_loss_tick_count`
- `--stop-loss-tick-multiplier`: config의 `risk.stop_loss_tick_multiplier`
- `--sell-tick-offset`: config의 `strategy.sell_tick_offset`
- `--start-buy-time`: config의 `market.start_buy_time`
- `--stop-buy-time`: config의 `market.stop_buy_time`
- `--force-sell-time`: config의 `market.force_sell_time`
- `--min-slot-count`: config의 `risk.min_slot_count`
- `--max-slot-count`: config의 `risk.max_slot_count`
- `--slot-budget-unit-krw`: config의 `risk.slot_budget_unit_krw`
- `--max-budget-per-stock-krw`: config의 `risk.max_budget_per_stock_krw`
- `--max-position-count`: config의 `risk.max_position_count`
- `--target-budget-ratio-per-stock`: config의 `risk.target_budget_ratio_per_stock`
- `--allow-refill-empty-slots`: 백테스트에서 빈 슬롯 재매수 허용
- `--disallow-refill-empty-slots`: 기본 동작 유지

백테스트에서 별도로 주의할 기본값:

- `--starting-capital-krw = 1000000`
- `--use-selected-signals = false`
- 라이브/리플레이 공통 fallback 규칙: 배치가 완전히 비어 있고 기본 `min_expected_return_percent` 기준 후보가 0개일 때만 `min_expected_return_fallback_percents = [0.6, 0.5]` 순서로 다시 후보를 고른다.

즉, 옵션 없이 리플레이를 돌리면 자본은 기본 `100만원`이고, 실제 저장된 선택 신호를 강제 재현하지 않는다.
