# KOSPI200 Weekly Pullback Bot v0.1

Real-trading update:
- `python main.py ... --real` now uses Kiwoom REST to submit live market orders.
- Live execution is implemented in `src/bot/execution/kiwoom_real.py`.
- `scan`, `buy`, and `monitor` still require a market snapshot CSV input.
- Real-run scripts look for `Weekly_bot\data\market_snapshot.csv` by default.
- To use a different file, set `WEEKLY_BOT_DATA_PATH` in your environment.

월요일 오전 10시에 KOSPI200 종목 중 눌림목 후보를 선별하고, 예수금의 90%를 최대 10종목에 균등 분배하여 매수한 뒤, 주중에는 익절/손절을 감시하고 금요일에는 남은 종목을 전량 청산하는 자동매매 봇 초안입니다.

> ⚠️ 이 프로젝트는 **실거래 베타용 골격**입니다. 기본 실행기는 `DryRunExecutor`이며 실제 주문은 발생하지 않습니다. 키움 OpenAPI+ 연동부는 `KiwoomExecutorStub`에 TODO 형태로 분리되어 있습니다.

---

## 전략 요약

### 매수 시점

- 매주 월요일 오전 10:00

### 대상 종목

- KOSPI200 구성종목

### 매수 필터

1. 시가총액 3,000억 원 이상
2. 당일 등락률 -1% ~ -10%
3. 거래대금/거래량 최소 기준 통과
4. 현재가가 Envelope 하단 2% 아래
5. 추세 조건 통과
   - `(MA30 우상향 OR MA50 우상향)`
   - `OR (MA120 우상향 AND 현재가 > MA120)`
6. 스프레드 `<= 2틱`

### 종목 수 / 자금 배분

- 최소 종목 수 제한 없음
- 최종 후보가 0개면 매수하지 않음
- 최종 후보가 1~10개면 전부 매수
- 최종 후보가 10개 초과면 점수 상위 10개 매수
- 예수금의 90%를 선정 종목 수만큼 균등 분배

### 매도 조건

- 익절: 매수가 대비 +2.5%
- 손절: 매수가 대비 -5.0%
- 금요일: 남은 보유 종목 전량 시장가 매도

---

## 폴더 구조

```text
kospi200_weekly_pullback_bot/
  main.py
  requirements.txt
  .env.example
  config/
    strategy.yaml
  data/
    sample_market_snapshot.csv
  logs/
    .gitkeep
  scripts/
    run_monday_scan_buy.ps1
    run_monitor.ps1
    run_friday_liquidate.ps1
  src/
    bot/
      config.py
      models.py
      runtime.py
      data/
        base.py
        csv_provider.py
      execution/
        base.py
        dry_run.py
        kiwoom_real.py
      risk/
        position_sizing.py
      strategy/
        weekly_pullback.py
  tests/
    test_strategy.py
```

---

## 빠른 실행

```bash
python -m venv .venv
source .venv/Scripts/activate  # Git Bash / Windows
pip install -r requirements.txt
python main.py scan --data data/sample_market_snapshot.csv
python main.py buy --data data/sample_market_snapshot.csv --cash 1000000
python main.py monitor --positions logs/positions.csv --data data/sample_market_snapshot.csv
python main.py friday-liquidate --positions logs/positions.csv
python main.py backtest --start 2024-01-01 --end 2024-12-31 --cash 10000000 --source auto --log-dir logs
```

PowerShell에서는 다음처럼 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe main.py scan --data data\sample_market_snapshot.csv
```

## Backtest

`backtest` 명령은 KOSPI200 현재 구성 종목을 기준으로 과거 일봉 데이터를 자동 수집해서 주간 전략을 간이 재현합니다.

- 기본 데이터 소스는 `auto`이며, `FinanceDataReader`를 우선 사용하고 실패 시 `yfinance` 가격 데이터를 시도합니다.
- 백테스트는 월요일 시가 매수, 화~금 일봉 고가/저가 기준 익절·손절 판정, 금요일 종가 강제청산으로 근사합니다.
- 과거 호가 데이터가 없기 때문에 스프레드는 1틱 매수호가/매도호가 차이로 단순화합니다.

예시:

```powershell
.\.venv\Scripts\python.exe .\Weekly_bot\main.py backtest --start 2024-01-01 --end 2024-12-31 --cash 10000000 --source auto --buy-slippage-bps 5 --sell-slippage-bps 5 --log-dir .\Weekly_bot\logs
```

생성 파일:

- `logs\backtests\summary.csv`
- `logs\backtests\trades.csv`
- `logs\backtests\weekly.csv`
- `logs\backtests\monthly.csv`

---

## 실제 키움 연동 시 할 일

`src/bot/execution/kiwoom_stub.py`의 TODO를 실제 키움 OpenAPI+ 호출로 교체해야 합니다.

필수 구현 항목:

1. 예수금 조회
2. 현재가/호가 조회
3. 시장가 매수 주문
4. 시장가 매도 주문
5. 보유잔고 조회
6. 주문 체결 확인
7. 미체결 주문 취소
8. 장중 감시 루프 안정화

실거래 전에는 반드시 다음 순서로 검증하세요.

1. CSV 데이터 기반 dry-run
2. 실시간 시세 조회만 연결
3. 주문 없는 실시간 paper trading
4. 1주/1종목/최소 금액 실거래
5. 전략 고정 후 4주 이상 로그 축적

---

## 로그 정책

이 봇은 전략 검증을 위해 다음 로그를 남기도록 설계되어 있습니다.

- 후보 종목 로그: `logs/candidates.csv`
- 주문 로그: `logs/orders.csv`
- 보유 포지션 로그: `logs/positions.csv`
- 매도 판단 로그: `logs/exit_decisions.csv`

초기 4주 동안은 조건을 자주 바꾸지 않는 것을 권장합니다. 조건을 바꾸면 전략 자체의 유효성 검증이 어려워집니다.
## Real Mode Examples

```powershell
.\.venv\Scripts\python.exe .\Weekly_bot\main.py buy --real --data .\Weekly_bot\data\market_snapshot.csv --log-dir .\Weekly_bot\logs
.\.venv\Scripts\python.exe .\Weekly_bot\main.py monitor --real --data .\Weekly_bot\data\market_snapshot.csv --log-dir .\Weekly_bot\logs
.\.venv\Scripts\python.exe .\Weekly_bot\main.py friday-liquidate --real --log-dir .\Weekly_bot\logs
```
