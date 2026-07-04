# Daily Bot - New PC Setup

이 문서는 다른 Windows PC에서 `Daily_bot` 실거래 환경을 다시 올릴 때 필요한 최소 체크리스트다.

## 1. Prerequisites

- Windows 10/11
- Python 3.10+
- Kiwoom REST API 사용 계정
- App Key / Secret
- 등록된 계좌번호
- 현재 PC의 허용 IP 등록

중요:

- 시스템 시간대는 `Asia/Seoul` 기준으로 맞춰 두는 것이 안전하다.
- 세션 시간 로직은 로컬 시간 기준으로 돈다.

## 2. Install

```powershell
cd C:\work
git clone <YOUR_REPO_URL>
cd StockAutoTradingBot

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. `.env`

위치:

```text
StockAutoTradingBot\.env
```

예시:

```dotenv
KIWOOM_APP_KEY=YOUR_APP_KEY
KIWOOM_APP_SECRET=YOUR_APP_SECRET
KIWOOM_ACCOUNT_NO=1234-5678-01
KIWOOM_BASE_URL=https://api.kiwoom.com

# Optional
# KIWOOM_DMST_STEX_TP=KRX
# KIWOOM_STEX_TP=1
# KIWOOM_QRY_TP=1
# KIWOOM_RATE_LIMIT_PER_SECOND=5
```

## 4. Current Key Settings

설정 파일:

- [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)

현재 핵심 운영값:

- `prewarm_start_time: 08:55`
- `start_buy_time: 09:30`
- `stop_buy_time: 11:30`
- `force_sell_time: 15:00`
- `reconcile_time: 15:15`
- `end_time: 15:20`
- `strategy.top_ratio: 1.0`
- `strategy.max_buy_count: 3`
- `strategy.min_expected_return_percent: 0.7`
- `strategy.min_expected_return_fallback_percent: 0.4`
- `strategy.max_spread_percent: 0.0`
- `strategy.min_prev_day_change_percent: 0.0`
- `strategy.max_prev_day_change_percent: 0.0`
- `risk.min_slot_count: 3`
- `risk.max_slot_count: 10`
- `risk.slot_budget_unit_krw: 5000000`
- `risk.stop_loss_percent: 4.5`

주의:

- `--real`은 실브로커를 사용한다.
- `--dry-run`은 모의 클라이언트를 사용한다.

## 5. Manual Run

드라이런:

```powershell
.\.venv\Scripts\Activate.ps1
python .\Daily_bot\main.py --dry-run
```

실거래:

```powershell
.\.venv\Scripts\Activate.ps1
python .\Daily_bot\main.py --real
```

## 6. Expected Runtime Flow

실행 후 일반 흐름:

1. 인증
2. 계좌 상태 조회
3. 프리웜과 유니버스 준비
4. 장중 스캔과 주문
5. `15:00` 강제 청산
6. `15:15` 브로커 체결 대조
7. `15:20` 종료

즉 `15:00`에 끝나는 것이 아니라 `15:15` 대조까지 기다린다.

## 7. Task Scheduler

기본 실행 스크립트:

- `Daily_bot\scripts\run_real.ps1`

예시:

```powershell
$scriptPath = "C:\work\StockAutoTradingBot\Daily_bot\scripts\run_real.ps1"
$taskCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

schtasks /Create /TN "StockAutoTradingBot-Real" `
  /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:05 `
  /TR $taskCmd /F
```

확인:

```powershell
schtasks /Query /TN "StockAutoTradingBot-Real" /V /FO LIST
```

## 8. Useful Checks

최신 로그:

```powershell
Get-ChildItem .\Daily_bot\logs | Sort-Object LastWriteTime -Descending | Select-Object -First 10
```

체결/감사:

- `Daily_bot\bot.sqlite3`
- `Daily_bot\logs\fills_YYYYMMDD.csv`
- `Daily_bot\logs\trade_fills_audit.csv`

백테스트 결과:

- `Daily_bot\backtest\results`

## 9. Backtest Reminder

현재 리플레이 기본 동작:

- 설정 파일 기본값을 그대로 읽음
- 시작자본 기본값은 `100만원`
- `--use-selected-signals` 기본값은 `False`
- `--fallback-min-expected-return` 기본값은 config의 `strategy.min_expected_return_fallback_percent`
- 라이브/리플레이 공통으로, 배치가 완전히 비어 있고 기본 기대수익률 문턱에서 후보가 0개일 때만 fallback 문턱으로 한 번 더 후보를 고른다.

실거래 비교용 리플레이라면 보통 다음처럼 명시해서 쓴다.

```powershell
.\.venv\Scripts\python.exe .\Daily_bot\backtest\replay_market_traces.py --db Daily_bot\bot.sqlite3 --use-selected-signals
```
