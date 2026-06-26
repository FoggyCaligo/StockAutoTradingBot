# Codex Handoff - Daily Bot

이 문서는 다음 작업자가 `Daily_bot`의 현재 실운영 상태와 최근 조정 포인트를 빠르게 파악하기 위한 인수인계 문서다.

## 1. 현재 운영 상태

- 봇은 `Daily_bot/scripts/run_real.ps1`로 실운영한다.
- 실운영 엔트리포인트는 `Daily_bot/main.py --real`이다.
- 세션 시간은 `09:10 ~ 11:30` 신규 매수, `15:00` 강제 청산, `15:15` 체결 대조, `15:20` 종료다.
- 실운영 설정은 반드시 [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)을 기준으로 본다.

## 2. 현재 핵심 설정 스냅샷

- `trend_filter.enabled = false`
- `strategy.top_ratio = 1.0`
- `strategy.min_expected_return_percent = 0.3`
- `strategy.max_spread_percent = 0.0`
- `strategy.min_prev_day_change_percent = -1.0`
- `strategy.max_prev_day_change_percent = 0.0`
- `strategy.max_intraday_jump_from_prev_scan_percent = 0.0`
- `risk.stop_loss_percent = 4.5`
- `risk.stop_loss_tick_count = 0`
- `risk.stop_loss_tick_multiplier = 0.0`
- `risk.max_orderbook_ask_depth_ratio = 0.0`

## 3. 파일 구조에서 중요한 지점

- [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py)
  메인 루프, 스캔, 진입, 강제청산, EOD reconciliation
- [broker/kiwoom_client.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/broker/kiwoom_client.py)
  Kiwoom REST API 래퍼
- [storage/db.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/storage/db.py)
  SQLite 저장, CSV export, 감사용 리빌드
- [risk/stop_loss.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/risk/stop_loss.py)
  손절 체크와 실행
- [risk/force_sell.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/risk/force_sell.py)
  장마감 강제 청산
- [backtest/replay_market_traces.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/backtest/replay_market_traces.py)
  리플레이 백테스트 엔진

## 4. 반드시 보존해야 하는 동작

1. 매수 체결 후 즉시 목표가 매도 주문을 건다.
2. `15:00` 이후 신규 진입은 막는다.
3. `15:15`에 브로커 체결을 다시 읽어 로컬 `fills`를 보정한다.
4. 추정 매도 체결 `sell_reconciliation`은 실제 브로커 체결로 대체되면 정리되어야 한다.
5. 운영 로그와 리포트 CSV는 장중 흐름과 EOD 정산을 모두 추적할 수 있어야 한다.

## 5. 오늘 기준 중요한 운영 메모

### 2026-06-25

- `reconcile_broker_fills()`가 EOD 보정 후 `purge_superseded_sell_reconciliation_fills()`까지 호출하도록 수정했다.
- 실운영 점검 결과, 후보 스캔은 정상적으로 돌고 있었지만 필터가 강해서 최종 후보가 0개 또는 1개 수준으로 줄어드는 상태였다.
- `min_expected_return_percent`를 `0.6 -> 0.3`으로 낮췄다.
- 이후 `min_prev_day_change_percent`를 `-1.5 -> -1.0`으로 다시 조정했다.
- 실운영 봇은 설정 변경 후 재시작했다.

## 6. 리플레이 해석 시 주의

현재 가장 중요한 함정은 멀티데이 리플레이다.

- `bot.sqlite3`에는 당일 데이터만 남아 있을 수 있다.
- 멀티데이 검증은 `logs/market_traces_*.csv`와 `logs/account_traces_*.csv`를 사용해 리플레이 DB를 재구성해야 한다.
- 하지만 오래된 `market_traces` CSV에는 `prev_day_change_percent`가 없는 날짜가 있다.
- 현재 라이브 설정은 `prev_day_change_percent`를 필터에 직접 사용하므로, 옛 로그에 그대로 적용하면 과거 날짜가 전부 탈락할 수 있다.

즉:

- “현재 라이브 설정 그대로의 멀티데이 리플레이”와
- “과거 데이터에 맞춘 비교용 리플레이”

를 구분해서 봐야 한다.

## 7. 다음 작업자가 바로 할 수 있는 점검

실운영 프로세스 확인:

```powershell
Get-CimInstance Win32_Process | Where-Object {
  $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'Daily_bot\\main.py'
}
```

최신 로그 확인:

```powershell
Get-ChildItem .\Daily_bot\logs\run_real* | Sort-Object LastWriteTime -Descending | Select-Object -First 10
```

당일 후보/선택 건수 확인:

```powershell
.\.venv\Scripts\python.exe - <<'PY'
import sqlite3
conn = sqlite3.connect("Daily_bot/bot.sqlite3")
cur = conn.cursor()
print(cur.execute("select count(*) from market_traces where session_date='2026-06-25' and phase='scan_candidate'").fetchone())
print(cur.execute("select count(*) from signals where substr(created_at,1,10)='2026-06-25' and selected=1").fetchone())
conn.close()
PY
```
