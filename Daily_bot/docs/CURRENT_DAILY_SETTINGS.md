# Current Daily Bot Settings

As of 2026-06-26, the live bot and replay backtest defaults are aligned to the same entry filter and stop-loss settings.

## Core Filters

- `strategy.top_ratio = 1.0`
- `strategy.min_expected_return_percent = 0.7`
- `strategy.max_spread_percent = 0.0`
- `strategy.spread_expected_return_multiplier = 0.0`
- `strategy.min_prev_day_change_percent = -0.1`
- `strategy.max_prev_day_change_percent = 0.0`
- `strategy.max_intraday_jump_from_prev_scan_percent = 0.0`

## Timing

- `market.start_buy_time = 09:30`
- `market.stop_buy_time = 11:30`
- `market.force_sell_time = 15:00`
- `strategy.scan_interval_seconds = 60`

## Risk

- `risk.max_position_count = 10`
- `risk.min_slot_count = 3`
- `risk.slot_budget_unit_krw = 5000000`
- `risk.max_slot_count = 10`
- `risk.target_budget_ratio_per_stock = 0.50`
- `risk.max_orderbook_ask_depth_ratio = 0.0`
- `risk.stop_loss_tick_count = 0`
- `risk.stop_loss_tick_multiplier = 0.0`
- `risk.stop_loss_percent = 4.5`
- `risk.daily_loss_limit_percent = 10.0`

## Latest Replay Check

Using the latest 2026-06-26 market trace snapshot checked during this session, replaying with `min_expected_return = 0.7` produced:

- today only: `6 trades`, `5 wins`, `1 loss`, `+13,965 KRW`
- 2026-06-25 to 2026-06-26: same result, with actual trades only on `2026-06-26`
