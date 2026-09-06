# Daily Bot

현재 전략을 빠르게 이해하기 위한 입구 문서다. **실제 동작의 진실원천은 코드와 설정**이며, 우선순위는 다음과 같다.

1. `Daily_bot/config/settings.yaml`
2. `Daily_bot/session_slot_runner.py`
3. `Daily_bot/main.py`
4. `Daily_bot/risk/stop_loss.py`
5. `Daily_bot/backtest/replay_refill_threshold.py`

과거 실험 기록은 `Diary.md`, `memo.txt`에 남아 있으며 현재 활성 전략과 혼동하지 않는다.

## 현재 전략 한 줄 요약

KOSPI와 KOSDAQ 전체에서 유동성이 충분한 종목을 대상으로 20호가 잔량에서 단기 균형가격을 추정하고, **최초 진입은 기대수익률 0.71% 이상**, **익절로 반환된 슬롯의 재진입은 0.90% 이상**일 때만 매수한다. 슬롯은 당일 시작자본에 따라 계산되고, 익절 슬롯은 11:30 전까지 재사용할 수 있지만 dynamic stop이 발생한 슬롯은 그 거래일 동안 폐쇄된다.

## 현재 활성 운영값

- 시장: `KOSPI + KOSDAQ`
- 시가총액 하한: `2,500억 원`
- 거래대금 하한: `30억 원`
- 스캔 주기: `60초`
- 신규매수 시간: `09:30 ~ 11:30`
- 장 시작 전 이월 포지션 정리: `09:10`
- 강제청산: `15:15`
- 체결/계좌 재정합: `15:20`
- 종료: `15:25`
- 최초/미사용 슬롯 기대수익률 기준: `0.71%`
- 익절 반환 슬롯 재진입 기준: `0.90%`
- `top_ratio = 1.0`: 랭킹 비율 컷 사실상 비활성
- fallback: 비활성
- 전일 등락률 필터: 비활성
- 스프레드 필터: 비활성
- 직전 스캔 급등 필터: 비활성
- 매도호가 깊이 제한: 비활성
- 스캔당 신규매수 상한: `3종목`
- 슬롯 수: 당일 시작자본 기준, 최소 `3`, 최대 `10`
- 슬롯 계산 단위: `500만 원`
- 총 보유 하드 상한: `10종목`
- 고정 장중 손절: 비활성
- dynamic expected-return stop: 기본 `-0.1%` 이하가 `3회 연속`
- 일손실 제한: `10%`

## 호가 기반 예상가

전략의 핵심 신호는 차트 지표가 아니라 20호가 잔량이다.

- 현재가에 가까운 호가잔량은 크게 반영한다.
- 멀어질수록 선형으로 가중치를 낮춘다.
- 매수/매도 양쪽 모두 `1.0 -> 0.0` 대칭 감쇠를 사용한다.
- 가장 먼 호가 레벨은 가격 레벨 자체는 남지만 잔량 가중치가 `0`이므로 예상가 계산에 실질적으로 기여하지 않는다.
- 감쇠된 호가잔량으로 균형가격 `expect_price`를 다시 계산한다.
- 실제 목표 매도가는 예상가에서 `sell_tick_offset = 1`틱을 뺀 값이다.

즉 “중간값에서 멀수록 호가잔량의 영향력을 줄이는” 처리가 현재 활성 상태다.

## 슬롯과 재진입 정책

현재 전략은 고정 3종목 전략이 아니다.

- 세션 시작 시 주문가능 자본을 기준으로 총 슬롯 수를 계산한다.
- 자본이 작아도 최소 3슬롯을 유지한다.
- 자본이 증가하면 슬롯은 늘 수 있으며 최대 10슬롯이다.
- 한 번의 스캔에서는 최대 3종목까지만 새로 산다.

빈 슬롯은 두 종류로 구분한다.

### 아직 사용하지 않은 슬롯

해당 세션에서 아직 채워본 적 없는 슬롯이다. 기존 진입 기준인 `0.71%`를 적용한다.

### 익절로 반환된 슬롯

한 번 사용했다가 `take_profit`으로 비워진 슬롯이다. 11:30 전이면 다시 사용할 수 있지만 기대수익률이 `0.90%` 이상이어야 한다.

현재 **full-batch lock은 없다**. 즉 전체 슬롯이 한 번 꽉 찼더라도 익절로 자리가 비면 11:30 전까지 다시 사용할 수 있다.

## 손절과 슬롯 폐쇄

고정 퍼센트/틱 손절은 현재 꺼져 있다. 대신 보유 종목도 호가를 다시 읽어 예상가를 재계산한다.

기본 dynamic stop 조건:

```text
expected_return <= -0.1%
3회 연속 관측
```

조건이 충족되면 기존 매도주문을 취소하고 실행 가능한 매도호가 근처로 청산한다.

추가 정책:

- 손절된 종목은 같은 날 재진입하지 않는다.
- 손절이 발생한 슬롯은 **그 거래일 동안만 폐쇄**된다.
- 다음 거래일에는 손절 슬롯 폐쇄 기록을 버리고 새 시작자본으로 슬롯 수를 다시 계산한다.

백테스트에는 실험용 `entry-anchor expected stop` 옵션도 있으나 **기본값은 비활성화(None)** 이며 실거래 전략에는 포함하지 않는다. 이 옵션은 재예측 매도가를 최초 매수가와 비교해 일정 기준 이하가 연속 발생할 때 청산하는 실험용 기능이다.

## 목표가와 장마감

정상 진입 후에는 즉시 목표 지정가 매도를 건다.

- 목표가 도달: 익절
- dynamic stop 발생: 조기 청산 + 당일 슬롯 폐쇄
- 15:15까지 남은 포지션: 강제청산
- 15:20: 브로커 체결내역과 로컬 기록 재정합

## 실거래 실행

권장 실행 경로는 `main.py` 직접 실행이 아니라 세션 슬롯 정책을 설치하는 `session_slot_runner.py`다.

```powershell
.\Daily_bot\scripts\run_real.ps1
```

직접 실행할 경우:

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\session_slot_runner.py --real
```

## 런타임 로그 파일 정책

실거래 실행 시 `Daily_bot/logs`에 지속적으로 남기는 기록은 다음 3종으로 제한한다.

- `market_traces_YYYYMMDD.csv`: 백테스트/판정 재현용 시장 trace
- `fills_YYYYMMDD.csv`: 브로커에서 실제 체결이 확인된 BUY/SELL만 기록하며 `ticker`와 종목명 `name`을 함께 저장. reconciliation 추정 fill은 CSV에서 제외
- `run_real_YYYYMMDD_HHMMSS.log`: 해당 실거래 세션의 콘솔 실행 로그

`account_traces_*`, `daily_reference_prices_*`, `orders_*`, `daily_rev.csv`, `trade_fills_audit*.csv`, `kt00001_cash_debug_*` 같은 별도 런타임 파일은 더 이상 생성하지 않는다. 필요한 내부 상태는 SQLite에 유지하므로 거래 로직 자체는 그대로 동작한다.

## 현재 전략과 맞춘 백테스트

**표준 백테스트 진입점은 하나만 사용한다.**

```text
Daily_bot/backtest/replay_refill_threshold.py
```

이 러너는 아래 정책을 순서대로 포함한다.

1. 기본 market trace replay
2. dynamic expected-return stop
3. 손절 슬롯 당일 폐쇄 + 같은 종목 당일 재진입 금지
4. 익절 반환 슬롯만 `0.90%` 이상의 더 엄격한 재진입 기준 적용

현재 설정을 모두 명시한 표준 실행 예시는 다음과 같다. 아래 값들은 **현재 기본값/실거래 대응값을 일부러 전부 적어 둔 것**이므로, 실험할 때는 바꾸려는 옵션만 수정하면 된다.

`--logs-dir Daily_bot/logs`를 명시하면 백테스트 시작 시 기존 replay cache를 재사용하지 않고, 그 시점에 존재하는 `market_traces_*.csv` 전체를 다시 읽어 replay DB를 재생성한다. 따라서 이후 거래일의 market trace 파일이 추가되어도 아래 표준 커맨드를 그대로 다시 실행하면 새 로그까지 자동 포함된다. 별도로 cache DB를 삭제할 필요는 없다.

```bash
python -m Daily_bot.backtest.replay_refill_threshold \
  --config Daily_bot/config/settings.yaml \
  --db bot.sqlite3 \
  --logs-dir Daily_bot/logs \
  --min-expected-return 0.71 \
  --refill-min-expected-return 0.90 \
  --fallback-min-expected-returns "" \
  --max-spread 0.0 \
  --min-prev-day-change 0.0 \
  --max-prev-day-change 0.0 \
  --max-intraday-jump-from-prev-scan 0.0 \
  --top-n 0 \
  --top-ratio 1.0 \
  --take-profit 0.4 \
  --stop-loss 0.0 \
  --stop-loss-tick-count 0 \
  --stop-loss-tick-multiplier 0.0 \
  --sell-tick-offset 1 \
  --start-buy-time 09:30 \
  --stop-buy-time 11:30 \
  --force-sell-time 15:15 \
  --max-hold-seconds-before-exit 0 \
  --spread-expected-return-multiplier 0.0 \
  --max-orderbook-ask-depth-ratio 0.0 \
  --missing-ask-depth-policy ignore \
  --trend-filter-disabled \
  --starting-capital-krw 1000000 \
  --min-slot-count 3 \
  --max-slot-count 10 \
  --slot-budget-unit-krw 5000000 \
  --max-budget-per-stock-krw 0 \
  --max-position-count 10 \
  --max-buy-count 3 \
  --target-budget-ratio-per-stock 0.50 \
  --ignore-selected-signals \
  --ignore-actual-fill-exits \
  --allow-refill-empty-slots \
  --refill-min-empty-fraction 0.0 \
  --block-stop-loss-reentry-same-day \
  --orderbook-levels-per-side 10 \
  --orderbook-bid-linear-decay-min-weight 0.0 \
  --orderbook-ask-linear-decay-min-weight 0.0 \
  --dynamic-expect-stop-threshold -0.1 \
  --dynamic-expect-stop-consecutive 3 \
  --entry-anchor-stop-consecutive 3 \
  --out Daily_bot/backtest/results/backtest_replay_live_slot_policy.csv
```

### 옵션 기본값과 주의사항

- `--entry-anchor-stop-threshold`: 기본값 `None`(비활성). 이 옵션은 기본 실행 명령에 넣지 않는다. 실험할 때만 예: `--entry-anchor-stop-threshold -0.5`처럼 추가한다.
- `--entry-anchor-stop-consecutive`: 기본 `3`. threshold가 비활성인 동안에는 결과에 영향이 없다.
- `--fallback-min-expected-return VALUE`: 여러 번 지정할 수 있는 단일 fallback 옵션이다. 현재 기본 fallback 목록은 비어 있다.
- `--fallback-min-expected-returns CSV`: 쉼표 구분 fallback 목록. 현재 기본값은 빈 문자열이다.
- `--use-selected-signals` / `--ignore-selected-signals`: 기본은 `ignore`.
- `--use-actual-fill-exits` / `--ignore-actual-fill-exits`: 기본은 `ignore`.
- `--allow-refill-empty-slots` / `--disallow-refill-empty-slots`: 현재 기본은 `allow`.
- `--block-stop-loss-reentry-same-day` / `--allow-stop-loss-reentry-same-day`: live-equivalent 러너는 기본적으로 `block`을 주입한다.
- `--trend-filter-enabled` / `--trend-filter-disabled`: 현재 설정 기본은 `disabled`.
- `--missing-ask-depth-policy`: `ignore` 또는 `skip`, 현재 기본 `ignore`.
- `--out`: 결과 trade CSV. 같은 이름에서 `_daily_rev.csv`, `_trade_fills_audit_daily.csv`도 파생 생성된다.

`take-profit=0.4`는 예상가가 유효하지 않을 때 사용하는 fallback 목표수익률이다. 정상적인 현재 전략에서는 호가 기반 `expect_price`에서 목표가를 계산하므로 일반적인 고정 TP 0.4% 전략이라는 뜻이 아니다.

백테스트는 다음을 맞춘다.

- 20호가 감쇠 계산
- `0.71%` 최초 진입 기준
- `0.90%` 익절 재진입 기준
- dynamic stop `-0.1% / 3회`
- 손절 슬롯 당일 폐쇄
- 손절 종목 당일 재진입 금지
- 자본 기반 슬롯 계산
- 11:30 신규매수 종료
- 예상가 -1틱 목표매도

다만 60초 사이 순간 체결, 브로커 내부 체결 순서, 부분체결과 취소/재주문의 모든 세부 흐름까지 완전히 복제하지는 못한다.

### 비용 반영

일별 수익 보고서(`*_daily_rev.csv`)의 `total_profit_krw`, `total_return_percent`, `total_return_percent_on_starting_capital`에는 비용이 반영된다.

현재 기본 비용은:

- 매수 수수료: `0.015%`
- 매도 수수료: `0.015%`
- 매도세: `0.18%`

즉 대략적인 왕복 비용은 매매금액 기준 약 `0.21%`다.

반면 trade CSV의 `pnl_percent`와 콘솔의 `avg_pnl`, `summed_pnl`, 단순 승률은 **가격 차이만 계산한 gross 지표**다. 실제 전략 평가에서는 `*_daily_rev.csv`의 비용후 성과와 별도로 계산한 net trade 통계를 함께 본다.

현재 main 로그 표본에서 현재 전략의 비용후 기준 결과는 다음과 같다.

- 거래 수: `44`
- gross 승/패: `31 / 13`, gross 승률 `70.45%`
- 비용후 승/패: `28 / 16`, 비용후 승률 `63.64%`
- 비용후 평균 승리: `+0.7747%`
- 비용후 평균 실패: `-0.7072%`
- 비용후 payoff ratio: `1.095 : 1`
- 비용후 평균 거래수익률: `+0.2358%`
- 비용후 순이익: `+36,831원`
- 비용후 일별 복리수익률: `+3.3244%`
- MDD: `-1.4730%`

표본은 아직 작으므로 위 수치는 현재 전략 검증용 기준선이지 장기 기대수익률의 확정치가 아니다.

## 관련 문서

- `docs/strategy_design.md`: 전략 철학과 의도
- `docs/CURRENT_DAILY_SETTINGS.md`: 현재 설정값 요약
- `docs/DAILY_BOT_LOGIC_REFERENCE.md`: 로직 상세 설명
- `docs/CODEX_HANDOFF.md`: 다음 작업자를 위한 압축 메모
- `Diary.md`: 과거 변경/실험 기록
