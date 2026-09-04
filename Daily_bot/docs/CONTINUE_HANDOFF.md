# Daily Bot - CONTINUE HANDOFF

## 현재 상태

실거래와 백테스트는 현재 아래 정책을 기준으로 맞춘다.

- KOSPI + KOSDAQ
- 최초/미사용 슬롯 기대수익률 `0.71%`
- 익절 반환 슬롯 재진입 기대수익률 `0.90%`
- `top_ratio = 1.0` → 랭킹 비율 컷 OFF
- fallback OFF
- 전일 등락률 필터 OFF
- full-batch lock 없음
- 익절 슬롯은 11:30 전까지 반복 재사용 가능
- dynamic stop 기본 `-0.1% 이하 3회 연속`
- dynamic stop/손절 슬롯은 당일 폐쇄
- 손절 종목은 당일 재진입 금지
- 호가잔량은 매수/매도 양쪽 모두 `1.0 -> 0.0` 선형 감쇠

## 지금 봐야 할 파일

- 실거래 실행: `Daily_bot/scripts/run_real.ps1`
- 슬롯 정책: `Daily_bot/session_slot_runner.py`
- 런타임 핵심 루프: `Daily_bot/main.py`
- 설정: `Daily_bot/config/settings.yaml`
- dynamic stop: `Daily_bot/risk/stop_loss.py`
- 호가 계산: `Daily_bot/strategy/orderbook_predictor.py`
- 기대수익 계산: `Daily_bot/strategy/signal.py`
- 슬롯 정책 백테스트: `Daily_bot/backtest/replay_dynamic_expect_debounce_reserve_stopped_slots.py`
- 0.90% 재진입 백테스트: `Daily_bot/backtest/replay_refill_threshold.py`
- 상세 전략 문서: `Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md`

## 슬롯 정책에서 특히 주의할 점

- `max_buy_count = 3`은 총 보유 상한이 아니라 **스캔당 신규 진입 상한**이다.
- 총 슬롯 수는 당일 시작 주문가능 자본으로 계산되며 최소 3, 최대 10이다.
- 아직 사용하지 않은 슬롯은 0.71% 기준이다.
- 익절로 반환된 슬롯만 0.90% 기준을 적용한다.
- 전체 슬롯이 한 번 꽉 찼다는 이유로 refill을 막지 않는다.
- 손절 슬롯은 영구 폐쇄가 아니라 **그 거래일에만 폐쇄**된다.
- 다음 거래일에는 슬롯 폐쇄 상태를 초기화한다.

## 백테스트 기준

현재 비교 기준 실행 예시:

```bash
python -m Daily_bot.backtest.replay_refill_threshold \
  --refill-min-expected-return 0.90 \
  --stop-buy-time 11:30 \
  --logs-dir Daily_bot/logs
```

최근 동일 조건 baseline은 대략 `38 trades / 71.05% win rate / +25,803 KRW / +2.2889% compounded / -1.0570% MDD`였다. 표본이 작으므로 절대 성과가 아니라 변경 전후 비교 기준으로 사용한다.

## 이어서 작업할 때 주의할 점

- 호가 기대수익 계산식을 바꾸면 실코드와 백테스트를 반드시 같이 수정한다.
- 슬롯 정책을 바꾸면 `session_slot_runner.py`와 두 replay 러너를 같이 본다.
- `settings.yaml`만 바꿔도 문서에 적힌 기본값과 충돌할 수 있으므로 `CURRENT_DAILY_SETTINGS.md`도 같이 갱신한다.
- 실험 결과는 총손익뿐 아니라 거래 수, dynamic stop 수, 강제청산 비중, MDD를 함께 본다.
