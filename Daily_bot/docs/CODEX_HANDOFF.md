# Codex Handoff - Daily Bot

이 문서는 다음 작업자가 현재 운영 상태를 빠르게 이어받기 위한 압축 메모다. 과거 실험값보다 **현재 실코드와 설정을 우선**한다.

## Current Runtime

- 실거래 권장 진입점: `Daily_bot/scripts/run_real.ps1`
- 실제 실행 모듈: `Daily_bot/session_slot_runner.py --real`
- 핵심 루프: `Daily_bot/main.py`
- 기본 설정: `Daily_bot/config/settings.yaml`
- dynamic stop: `Daily_bot/risk/stop_loss.py`
- 호가 예상가: `Daily_bot/strategy/signal.py`, `Daily_bot/strategy/orderbook_predictor.py`
- live-equivalent 슬롯 백테스트: `Daily_bot/backtest/replay_dynamic_expect_debounce_reserve_stopped_slots.py`
- 익절 재진입 임계값 백테스트: `Daily_bot/backtest/replay_refill_threshold.py`

## Current Strategy

- 시장: KOSPI + KOSDAQ
- 시총 하한: 2,500억 원
- 거래대금 하한: 30억 원
- 신규매수: 09:30~11:30
- 스캔: 60초
- 최초/미사용 슬롯 기대수익률: 0.71%
- 익절 반환 슬롯 재진입 기대수익률: 0.90%
- `top_ratio = 1.0`: 랭킹 비율 컷 OFF
- fallback OFF
- 전일 등락률 필터 OFF
- 스프레드 필터 OFF
- 직전 스캔 급등 필터 OFF
- 추세 필터 OFF
- 매도호가 깊이 필터 OFF
- full-batch lock 없음

## Slot Policy

- 슬롯 수는 세션 시작 주문가능 자본으로 계산한다.
- 최소 3, 최대 10슬롯.
- 500만원 단위로 슬롯 증가 가능.
- 한 스캔 신규매수는 최대 3종목.
- 미사용 슬롯은 0.71% 기준.
- 익절 슬롯은 11:30 전 0.90% 기준으로 반복 재사용 가능.
- dynamic stop/손절 슬롯은 그날만 폐쇄.
- 다음 거래일에는 폐쇄 슬롯을 복구하고 자본 기준으로 다시 계산.
- 손절 종목은 같은 날 재진입 금지.

## Orderbook Model

- 매수/매도 20호가 사용.
- 양쪽 모두 선형 감쇠 `1.0 -> 0.0`.
- 현재가에서 멀수록 호가잔량 영향력이 작다.
- 감쇠된 잔량으로 `expect_price` 자체를 계산한다.
- 목표 매도가는 예상가에서 1틱 낮춘 가격.

## Stop / Exit

고정 장중 손절은 모두 OFF:

```text
stop_loss_percent = 0
stop_loss_tick_count = 0
stop_loss_tick_multiplier = 0
```

Dynamic stop 기본값:

```text
DYNAMIC_EXPECT_STOP_PERCENT=-0.1
DYNAMIC_EXPECT_STOP_CONSECUTIVE=3
```

보유 중 기대수익률이 -0.1% 이하로 3회 연속이면 청산한다.

- 해당 종목 same-day re-entry block
- 해당 슬롯 same-day close
- 15:15 잔여 포지션 강제청산
- 15:20 브로커 체결 재정합

## Backtest Baseline

현재 전략 비교 기준은 다음과 같다.

```bash
python -m Daily_bot.backtest.replay_refill_threshold \
  --refill-min-expected-return 0.90 \
  --stop-buy-time 11:30 \
  --logs-dir Daily_bot/logs
```

최근 동일 조건 비교에서 기준선은 대략:

```text
38 trades
71.05% win rate
25,803 KRW net profit
+2.2889% compounded return
-1.0570% MDD
```

표본이 작으므로 이 숫자를 장기 기대수익으로 해석하지 않는다. 현재 전략 변경 비교용 baseline으로만 사용한다.

## Do Not Reintroduce Accidentally

현재는 아래 정책이 활성화되어 있지 않다.

- top 25% 컷
- 전일 +10% 상한
- full-batch lock
- 12:30 매수 마감
- 일부 포지션이 남아 있으면 모든 refill 차단
- dynamic stop 발생 후 모든 신규매수 일괄 금지

변경 전 반드시 `settings.yaml`, `session_slot_runner.py`, 백테스트 러너 세 곳을 같이 확인한다.

## Docs

- `Daily_bot/README.md`: 빠른 개요
- `docs/strategy_design.md`: 전략 철학
- `docs/CURRENT_DAILY_SETTINGS.md`: 활성값
- `docs/DAILY_BOT_LOGIC_REFERENCE.md`: 상세 실행 흐름
- `Diary.md`: 과거 실험 기록
