# Current Daily Bot Settings

이 문서는 현재 활성 설정값만 빠르게 확인하기 위한 요약이다. 개념 설명은 [curr_strategy.txt](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/curr_strategy.txt)와 [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)를 본다.

## Universe

- `universe.source = KOSPI`
- `universe.csv_path = ""`
- `universe.cache_path = data/kospi_latest.csv`
- `universe.refresh_daily = true`
- `universe.min_market_cap_krw = 250000000000`
- `universe.min_trading_value_krw = 3000000000`

## Strategy

- `strategy.top_ratio = 0.25`
- `strategy.max_buy_count = 3`
- `strategy.allow_refill_empty_slots = true`
- `strategy.min_expected_return_percent = 0.7`
- `strategy.min_expected_return_fallback_percents = []`
- `strategy.max_spread_percent = 0.0`
- `strategy.spread_expected_return_multiplier = 0.0`
- `strategy.min_prev_day_change_percent = 0.0`
- `strategy.max_prev_day_change_percent = 10.0`
- `strategy.max_intraday_jump_from_prev_scan_percent = 0.0`
- `strategy.orderbook_bid_linear_decay_min_weight = 0.0`
- `strategy.orderbook_ask_linear_decay_min_weight = 0.0`
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
- `risk.stop_loss_percent = 0.0`
- `risk.daily_loss_limit_percent = 10.0`
- 동적 예상수익률 손절 임계값: `.env`의 `DYNAMIC_EXPECT_STOP_PERCENT`, 기본 `-0.1%`, `off`로 비활성화 가능
- 동적 예상수익률 손절 연속 횟수: `.env`의 `DYNAMIC_EXPECT_STOP_CONSECUTIVE`, 기본 `3`

## Operational Meaning

- 현재 운영은 `0.7 단일`이다. fallback은 꺼져 있다.
- 현재 랭킹 컷은 `상위 25%`다.
- 현재 호가 기대수익률 계산은 매수/매도 양쪽 모두에 강한 대칭 선형 감쇠를 건다.
- 고정 장중 손절은 꺼져 있다.
- 동적 예상수익률 손절은 현재가 대비 예상수익률이 `-0.1%` 이하인 상태가 3회 연속 관측될 때 발동한다.
- 동적 예상수익률 손절이 한 번 발동하면 해당 거래일의 남은 신규매수는 차단하고, 이미 보유 중인 다른 포지션은 계속 관리한다.
- 일반적인 빈 슬롯 재매수는 허용되어 있지만, 동적 손절 발동일에는 예외적으로 그날 추가 신규매수를 하지 않는다.
- `max_buy_count = 3`은 총 보유 상한이 아니라 스캔당 신규 진입 상한이다.
- 총 보유 상한은 슬롯 계산과 `risk.max_position_count = 10`이 함께 결정한다.

## Backtest Alignment

- 백테스트 기본 기대수익률 기준은 config의 `strategy.min_expected_return_percent`를 따른다.
- 백테스트 fallback 기본값도 config의 `strategy.min_expected_return_fallback_percents`를 따른다.
- 백테스트 기본 고정 손절도 config의 `risk.stop_loss_*` 값을 그대로 따른다.
- 백테스트 기본 호가 감쇠도 config의 `strategy.orderbook_*_linear_decay_min_weight`를 그대로 따른다.
- 백테스트의 기준 DB는 `Daily_bot/backtest/cache/rebuild_from_logs_replay_from_logs.sqlite3`다.
- 이 DB는 `Daily_bot/logs/market_traces_*.csv`에서 복원한 전용 replay DB이며, 실거래 `Daily_bot/bot.sqlite3`와 분리한다.
- 일반 replay, 동적 예상수익률 진단, 설정 sweep 실행 시 모두 위 replay DB를 사용한다.
- 기본 거래 결과는 `Daily_bot/backtest/results/backtest_replay.csv`에 생성된다.
- 동적 예상수익률 진단 결과는 `Daily_bot/backtest/results/dynamic_expect_stop_diagnostics.csv`에 생성된다.
- 임계값 sweep 결과는 `Daily_bot/backtest/results/dynamic_expect_stop_threshold_sweep.csv`에 생성된다.
