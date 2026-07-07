# Codex Handoff - Daily Bot

이 문서는 `Daily_bot`의 현재 실코드와 백테스트 코드 상태를 빠르게 파악하기 위한 핸드오프 요약이다.

## 1. Current Runtime

- 진입점은 `Daily_bot/main.py --real`
- 실거래 실행 스크립트는 `Daily_bot/scripts/run_real.ps1`
- 세션 시간은 `08:55` 프리웜, `09:30 ~ 11:30` 신규 매수, `15:00` 강제 청산, `15:15` 체결 대조, `15:20` 종료
- 기준 설정 파일은 [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)

## 2. Current Live Settings

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

## 3. Important Behavior

1. 매수 체결 직후 목표가 지정가 매도를 낸다.
2. 목표 매도가는 진입가보다 최소 1틱 위 가격으로 보정된다.
3. 활성 포지션이나 미체결 주문이 하나라도 남아 있으면 새 배치를 다시 시작하지 않는다.
4. `15:00` 이후 신규 진입은 막고 강제 청산 흐름으로 넘어간다.
5. `15:15`에 브로커 체결내역을 다시 읽어 `fills`를 보정한다.
6. 체결 누락, 부분체결, 복구 매도 제출을 로그와 보조 로직으로 추적한다.

## 4. Important Files

- [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py)
  메인 루프, 스캔, 진입, 손절, 강제 청산, EOD reconciliation
- [broker/kiwoom_client.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/broker/kiwoom_client.py)
  Kiwoom REST API 래퍼
- [storage/db.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/storage/db.py)
  SQLite 저장과 CSV export
- [risk/stop_loss.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/risk/stop_loss.py)
  손절 체크와 실행
- [risk/force_sell.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/risk/force_sell.py)
  장마감 강제 청산
- [backtest/replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)
  리플레이 백테스트 엔진

## 5. Backtest Notes

- 백테스트 기본값은 `--config`의 설정 파일을 읽는다.
- `--starting-capital-krw` 기본값은 `1000000`
- `--use-selected-signals` 기본값은 `False`
- `--fallback-min-expected-returns` 기본값은 config의 `strategy.min_expected_return_fallback_percents`
- 따라서 옵션 없이 돌리면 실제 선택 신호를 그대로 재현하지 않고, `market_traces` 기준 후보를 다시 고른다.
- 리플레이도 라이브와 같은 fallback 규칙을 따라, 배치가 비어 있고 기본 문턱에서 후보가 0개일 때만 fallback 문턱으로 다시 고른다.
- 실거래 비교용이면 `--use-selected-signals` 여부를 명시하는 편이 좋다.

## 6. Replay Caveats

- `bot.sqlite3`만으로는 과거 전체 세션이 다 남아 있지 않을 수 있다.
- 멀티데이 검증은 `logs/market_traces_*.csv`와 `logs/account_traces_*.csv` 기반 리플레이 DB 재구성이 더 정확하다.
- 리플레이는 부분체결, 취소 후 추가체결, 복구 매도, 분할청산을 완전 재현하지 못한다.
- 실거래와 백테스트 손익 차이를 볼 때는 진입 종목 차이와 체결 단순화를 같이 봐야 한다.
