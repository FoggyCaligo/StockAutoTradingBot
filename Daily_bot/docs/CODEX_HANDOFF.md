# Codex Handoff - Daily Bot

이 문서는 `Daily_bot` 현재 운영 상태를 빠르게 이어받기 위한 짧은 인수인계 문서다. 자세한 개념 설명은 [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md) 를 본다.

## Current Runtime

- 진입점: [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py)
- 기본 설정 파일: [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)
- 실행 시간: `08:55` 프리웜, `09:30 ~ 11:30` 신규 진입, `15:00` 강제청산, `15:15` 체결 보정, `15:20` 종료

## Current Settings

- `trend_filter.enabled = false`
- `strategy.top_ratio = 1.0`
- `strategy.max_buy_count = 3`
- `strategy.min_expected_return_percent = 0.7`
- `strategy.min_expected_return_fallback_percents = [0.6, 0.5]`
- `strategy.max_spread_percent = 0.0`
- `strategy.min_prev_day_change_percent = 0.0`
- `strategy.max_prev_day_change_percent = 0.0`
- `strategy.max_intraday_jump_from_prev_scan_percent = 0.0`
- `risk.max_position_count = 10`
- `risk.min_slot_count = 3`
- `risk.slot_budget_unit_krw = 5000000`
- `risk.max_slot_count = 10`
- `risk.target_budget_ratio_per_stock = 0.50`
- `risk.stop_loss_percent = 4.5`
- `risk.stop_loss_tick_count = 0`
- `risk.stop_loss_tick_multiplier = 0.0`
- `risk.max_orderbook_ask_depth_ratio = 0.0`

## Operational Meaning

- `max_buy_count = 3` 은 총 보유 수가 아니라 한 번의 신규 진입 배치 상한이다.
- 총 보유 가능 종목 수는 자본 기반 슬롯 계산과 `risk.max_position_count = 10` 으로 결정된다.
- 기본 실거래는 일부 슬롯이 비어도 전체 배치가 비기 전까지 재진입하지 않는다.
- fallback 은 빈 배치에서만 `0.6 -> 0.5` 순서로 동작한다.

## Important Files

- [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py)
- [strategy/signal.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/strategy/signal.py)
- [risk/guards.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/risk/guards.py)
- [risk/stop_loss.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/risk/stop_loss.py)
- [risk/force_sell.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/risk/force_sell.py)
- [storage/db.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/storage/db.py)
- [backtest/replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)
- [backtest/replay_db_builder.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_db_builder.py)

## Backtest Notes

- 기본 리플레이는 `market_traces` 기반 시뮬레이션이다.
- 현재 리플레이는 `scan_cycle_at` 기준 배치 재구성, `scan_candidate` 기준 엔트리, fallback, 직전 스캔 급등 필터, `select_affordable_targets` 를 반영한다.
- `--use-selected-signals` 를 켜면 실제 선택 신호 기준 재현에 더 가깝게 볼 수 있지만, 설정값 변경 실험 자유도는 줄어든다.
- 여전히 부분체결, 주문 취소-재주문, 60초 사이 순간 고가/저가까지 완전히 같지는 않다.
