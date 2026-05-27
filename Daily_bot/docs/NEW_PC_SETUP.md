# StockAutoTradingBot - New PC Setup Guide (Windows)

This guide explains how to run this project on another Windows PC, including:
- `.env` format
- dependency install
- dry-run and real-trade execution
- Windows Task Scheduler registration

## 1. Prerequisites

- Windows 10/11
- Python 3.10+ installed
- Kiwoom REST API service enabled on your account
- App Key / Secret Key issued
- account registered in Kiwoom REST portal
- allowed IP registered in Kiwoom REST portal for this PC/network

Important:
- Set Windows time zone to `Korea Standard Time (Asia/Seoul)`.
  - Trading time checks in this project use local system time.

## 2. Clone and Initial Install

From PowerShell:

```powershell
cd C:\work
git clone <YOUR_REPO_URL>
cd StockAutoTradingBot

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Create `.env` (Required)

Create `.env` in repository root (`StockAutoTradingBot\.env`).

Example template:

```dotenv
KIWOOM_APP_KEY=YOUR_APP_KEY
KIWOOM_APP_SECRET=YOUR_APP_SECRET
KIWOOM_ACCOUNT_NO=1234-5678-01
KIWOOM_BASE_URL=https://api.kiwoom.com

# Optional tuning values
# KIWOOM_DMST_STEX_TP=KRX
# KIWOOM_STEX_TP=1
# KIWOOM_QRY_TP=1
# KIWOOM_RATE_LIMIT_PER_SECOND=5
```

Notes:
- `KIWOOM_BASE_URL`
  - Real trading: `https://api.kiwoom.com`
  - Mock domain (if needed for limited API tests): `https://mockapi.kiwoom.com`
- Do not commit `.env` to git.

## 4. Config Check (`Daily_bot/config/settings.yaml`)

Default config currently has:
- `risk.dry_run: true`
- force sell at `13:00`

Current runtime behavior:
- If started with `--real`, real Kiwoom client is used even if `risk.dry_run` is true.
- Bot exits after force-sell flow at 13:00 (it does not keep running for multiple days by itself).

## 5. Manual Run Test

### 5.1 Dry-run (safe test)

```powershell
.\.venv\Scripts\Activate.ps1
python .\Daily_bot\main.py --dry-run
```

### 5.2 Real trading

```powershell
.\.venv\Scripts\Activate.ps1
python .\Daily_bot\main.py --real
```

## 6. Scheduled Auto-Run (Weekdays 09:05)

This repo includes:
- `Daily_bot\scripts\run_real.ps1`
  - runs `Daily_bot\main.py --real`
  - writes logs to `Daily_bot\logs\run_real_YYYYMMDD_HHMMSS.log`

Register task (run once in elevated or allowed PowerShell):

```powershell
$scriptPath = "C:\work\StockAutoTradingBot\Daily_bot\scripts\run_real.ps1"
$taskCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

schtasks /Create /TN "StockAutoTradingBot-Real" `
  /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:05 `
  /TR $taskCmd /F
```

Verify:

```powershell
schtasks /Query /TN "StockAutoTradingBot-Real" /V /FO LIST
```

Run once immediately (manual trigger):

```powershell
schtasks /Run /TN "StockAutoTradingBot-Real"
```

Delete task:

```powershell
schtasks /Delete /TN "StockAutoTradingBot-Real" /F
```

## 7. Daily Operations

- Check latest logs:

```powershell
Get-ChildItem .\Daily_bot\logs | Sort-Object LastWriteTime -Descending | Select-Object -First 5
Get-Content .\Daily_bot\logs\<LATEST_LOG_FILE>
```

- Check scheduler status:

```powershell
schtasks /Query /TN "StockAutoTradingBot-Real" /V /FO LIST
```

## 8. Troubleshooting

- `Call auth() before API requests`
  - `.env` missing or invalid key/secret values.
- auth/403 errors
  - IP not registered in Kiwoom REST portal.
  - wrong `KIWOOM_BASE_URL` for your intended environment.
- task created but not running
  - user session/logon mode or power/sleep policy issue.
  - verify with `schtasks /Query ... /V /FO LIST`.
- no trades
  - outside buy window (`09:10` to `11:30`) or no candidate passed filters.
