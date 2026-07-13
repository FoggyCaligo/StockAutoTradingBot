# Current Daily Bot Settings

이 문서는 현재 실거래 기준 설정값만 간단히 요약한다. 개념 중심 설명은 [curr_strategy.txt](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/curr_strategy.txt)를 본다.

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
- `strategy.min_expected_return_fallback_percents = []`
- `strategy.max_spread_percent = 0.0`
- `strategy.spread_expected_return_multiplier = 0.0`
- `strategy.min_prev_day_change_percent = 0.0`
- `strategy.max_prev_day_change_percent = 1.0`
- `strategy.max_intraday_jump_from_prev_scan_percent = 0.0`
- `strategy.sell_tick_offset = 1`
- `strategy.scan_interval_seconds = 60`

## Market Times

- `market.prewarm_start_time = 08:55`
- `market.startup_clear_time = 09:10`
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

## Important Notes

- 현재 기대수익률 기준은 `0.7` 단일이다.
- fallback은 현재 비활성화되어 있다.
- 현재 `allow_refill_empty_slots = true` 이므로, 중간에 빈 슬롯이 생기면 이후 스캔에서 재매수할 수 있다.
- `max_buy_count = 3`은 전체 보유 수 상한이 아니라 한 번의 스캔당 신규 매수 상한이다.
- 총 보유 가능 종목 수는 자금 기반 슬롯 계산과 `risk.max_position_count = 10`으로 결정된다.
- 전일 급등 상한 필터는 현재 `1.0%`로 활성화되어 있다.
- 스프레드 필터, 직전 스캔 급등 억제 필터, 매도호가 잔량 비율 필터도 현재 꺼져 있다.

## Backtest Alignment

- 백테스트 기본 기대수익률은 config의 `strategy.min_expected_return_percent`를 따른다.
- 백테스트 fallback 기대수익률은 config의 `strategy.min_expected_return_fallback_percents`를 따른다.
- 백테스트에서 재매수 금지는 `--disallow-refill-empty-slots` 또는 동일 config 기준으로 맞춘다.
