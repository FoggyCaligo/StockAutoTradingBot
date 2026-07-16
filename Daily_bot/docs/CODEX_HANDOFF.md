# Codex Handoff - Daily Bot

이 문서는 다음 작업자가 현재 운영 상태를 빠르게 이어받기 위한 압축 메모다. 개념 설명은 [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)를 본다.

## Current Runtime

- 진입점: [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py)
- 기본 설정: [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)
- 스캔/예상가 계산: [strategy/signal.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/strategy/signal.py), [strategy/orderbook_predictor.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/strategy/orderbook_predictor.py)
- 백테스트 진입점: [replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)

## Current Active Settings

- `trend_filter.enabled = false`
- `strategy.top_ratio = 0.25`
- `strategy.max_buy_count = 3`
- `strategy.allow_refill_empty_slots = true`
- `strategy.min_expected_return_percent = 0.7`
- `strategy.min_expected_return_fallback_percents = []`
- `strategy.max_prev_day_change_percent = 10.0`
- `strategy.orderbook_bid_linear_decay_min_weight = 0.0`
- `strategy.orderbook_ask_linear_decay_min_weight = 0.0`
- `risk.max_position_count = 10`
- `risk.min_slot_count = 3`
- `risk.slot_budget_unit_krw = 5000000`
- `risk.max_slot_count = 10`
- `risk.target_budget_ratio_per_stock = 0.50`
- `risk.stop_loss_percent = 0.0`
- `risk.stop_loss_tick_count = 0`
- `risk.stop_loss_tick_multiplier = 0.0`
- `risk.daily_loss_limit_percent = 10.0`

## Operational Meaning

- 현재 운영은 `0.7 단일 + 상위 25% 컷 + 재매수 허용 + 전일 1% 상한 + 무손절`이다.
- 호가 모델은 매수/매도 양쪽 모두 강한 대칭 선형 감쇠를 건다.
- `max_buy_count = 3`은 총 보유 제한이 아니라 스캔당 신규 진입 제한이다.
- 총 보유 상한은 슬롯 계산 결과와 `risk.max_position_count = 10`이 함께 결정한다.
- 손절 후 당일 재진입 차단 코드는 남아 있지만, 현재 손절이 꺼져 있어 사실상 유휴 상태다.

## Backtest Notes

- 기본 리플레이도 위 설정을 그대로 따른다.
- 호가 감쇠는 실코드와 같은 공용 함수로 계산한다.
- 리플레이는 `raw_json` 기반 호가 재구성, `scan_cycle_at` 기준 배치 재구성, 자본 기반 조합 선택을 반영한다.
- 아직 60초 사이 순간 고가/저가, 부분체결, 브로커 내부 체결 순서까지 완전히 재현하지는 않는다.
