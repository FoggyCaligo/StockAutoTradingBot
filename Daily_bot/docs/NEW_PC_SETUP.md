# Daily Bot - 새 PC 세팅 가이드

이 문서는 다른 Windows PC에서 `Daily_bot` 실거래 환경을 다시 세팅할 때 필요한 절차를 정리한 문서입니다.

## 1. 준비물

- Windows 10/11
- Python 3.10 이상
- 키움 REST API 사용 승인 계정
- App Key / Secret
- 등록된 계좌번호
- 현재 PC 또는 네트워크의 허용 IP 등록

중요:

- 시스템 시간대는 `Asia/Seoul`로 맞춰두는 것이 안전하다.
- 이 봇은 로컬 시간 기준으로 장중/마감 로직을 판단한다.

## 2. 설치

```powershell
cd C:\work
git clone <YOUR_REPO_URL>
cd StockAutoTradingBot

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. `.env` 생성

저장 위치:

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

주의:

- 실거래 URL은 `https://api.kiwoom.com`
- `.env`는 git에 커밋하지 않는다.

## 4. 설정 파일 확인

설정 파일:

- [settings.yaml](/abs/path/c:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml)

현재 중요한 운영 설정:

- `start_buy_time: 09:30`
- `stop_buy_time: 14:00`
- `force_sell_time: 15:00`
- `reconcile_time: 15:15`
- `end_time: 15:20`
- `strategy.min_expected_return_percent: 0.3`
- `strategy.max_spread_percent: 0.5`
- `risk.stop_loss_percent: 3.0`

주의:

- `--real`로 실행하면 실거래 클라이언트를 사용한다.
- `--dry-run`으로 실행하면 설정값과 무관하게 모의 흐름으로 강제된다.

## 5. 수동 실행 점검

### 드라이런

```powershell
.\.venv\Scripts\Activate.ps1
python .\Daily_bot\main.py --dry-run
```

### 실거래

```powershell
.\.venv\Scripts\Activate.ps1
python .\Daily_bot\main.py --real
```

실행 후 확인 포인트:

- `Daily_bot\logs\run_real.lock`
- `Daily_bot\logs\*.log`
- `Daily_bot\bot.sqlite3`

## 6. 현재 런타임 동작

실거래 실행 시 봇은 보통 아래 순서로 움직인다.

1. 인증
2. 계좌 상태 조회
3. 유니버스 워밍업
4. 장중 후보 스캔 및 주문
5. `15:00` 강제청산
6. `15:15` 브로커 체결 대조
7. `15:20` 종료

즉, `15:00`에 바로 끝나지 않고 `15:15` 대조까지 기다린다.

## 7. 작업 스케줄러 등록

기본 실행 스크립트:

- `Daily_bot\scripts\run_real.ps1`

등록 예시:

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

즉시 실행:

```powershell
schtasks /Run /TN "StockAutoTradingBot-Real"
```

삭제:

```powershell
schtasks /Delete /TN "StockAutoTradingBot-Real" /F
```

## 8. 운영 중 확인할 것

### 최근 로그

```powershell
Get-ChildItem .\Daily_bot\logs | Sort-Object LastWriteTime -Descending | Select-Object -First 10
```

### 스케줄러 상태

```powershell
schtasks /Query /TN "StockAutoTradingBot-Real" /V /FO LIST
```

### 체결 원장

- `Daily_bot\bot.sqlite3`
- `Daily_bot\logs\fills_YYYYMMDD.csv`
- `Daily_bot\logs\trade_fills_audit.csv`

### 백테스트 결과 위치

- `Daily_bot\backtest\results`

### 신뢰 기준

- 장중 손익 확인: `MTS 우선`
- 마감 후 체결 정합성: `15:15 대조 후 로컬 기록 우선`

## 9. 문제 해결

### `Call auth() before API requests`

- `.env` 값 누락 또는 잘못된 키/시크릿

### `403` 또는 인증 실패

- 허용 IP 미등록
- 실거래/모의 URL 혼동

### 작업 스케줄러는 있는데 실행이 안 됨

- 슬립/절전 정책
- 사용자 권한
- `schtasks /Query ... /V /FO LIST`로 마지막 실행 결과 확인

### 장중 거래가 전혀 없음

- 매수 가능 시간 아님
- 후보 필터를 통과한 종목 없음
- 계좌에 기존 보유 또는 미체결 주문이 남아 있어 신규 스캔이 막힘

### `trade_fills_audit.csv` 총손익이 MTS와 다르게 보임

- 이 파일은 종목별 누적 감사용 원장이다.
- `SELL` 행 전체를 단순 합산하면 안 된다.
- 총손익은 브로커 API 또는 별도 요약 로직으로 봐야 한다.
