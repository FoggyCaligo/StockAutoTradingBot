# KOSPI200 Weekly Pullback Bot v0.1

실거래 업데이트:

- `python main.py ... --real`은 키움 REST를 통해 실제 주문을 전송합니다.
- 실거래 실행기는 `src/bot/execution/kiwoom_real.py`에 구현되어 있습니다.
- `scan`, `buy`, `monitor`는 `--data live`로 실시간 데이터 기반 실행이 가능합니다.
- 스크립트 실행 시 기본 스냅샷 경로는 `Weekly_bot\data\market_snapshot.csv`입니다.
- 다른 파일을 쓰려면 환경변수 `WEEKLY_BOT_DATA_PATH`를 지정하면 됩니다.
- 현재 운영 기준 문서는 `Weekly_bot/CURRENT_RULES.md`, `Weekly_bot/strategy.txt`입니다.

월요일 오전 10시에 KOSPI200 종목 중 눌림목 후보를 선별하고, 가용 현금의 90%를 활용해 후보 종목들에 균등 분배하여 매수합니다. 월요일 매수 후 빈 슬롯이 남아 있으면 화요일에도 `buy`를 한 번 더 실행해 이미 보유 중인 종목을 제외하고 추가 매수를 진행할 수 있습니다. 주중에는 익절과 손절을 감시하고, 금요일에는 남은 종목을 전량 청산하는 주간 자동매매 봇입니다.

> 현재는 실거래 경로까지 연결되어 있으며, 주간 스케줄러는 월요일 1차 매수, 화요일 추가 매수, 주중 모니터링, 금요일 강제청산 기준으로 맞춰져 있습니다.

---

## 전략 요약

### 매수 시점

- 1차 매수: 매주 월요일 오전 10:00
- 보충 매수: 매주 화요일 오전 10:00, 월요일 매수 후 `max_positions` 기준 빈 슬롯이 남아 있을 때만 실행

### 대상 종목

- KOSPI200 구성종목

### 매수 필터

1. 시가총액 3,000억 원 이상
2. 전일 등락률 -7.0% ~ -2.0%
3. 거래대금 10억 원 이상
4. 현재가가 Envelope 하단 기준 아래
5. 추세 조건 통과
   - `(MA30 우상향 OR MA50 우상향)`
   - `OR (MA120 우상향 AND 현재가 > MA120)`
6. 스프레드 필터는 현재 기본 비활성화

### 종목 수 / 자금 배분

- 목표 최대 종목 수는 10개
- `min_positions=5`는 참고용 목표치이며 강제 조건은 아님
- 최종 후보가 0개면 매수하지 않음
- 최종 후보가 1개 이상이면 자금과 종목 가격을 기준으로 실제 매수 가능한 수량만큼 진입
- 가용 현금의 90%를 최종 선정 종목 수만큼 균등 분배
- 자본이 부족해 5종목을 채울 수 없더라도 매수 가능한 종목은 그대로 매수
- 보충 매수 시 현재 보유 중인 종목 코드는 후보에서 제외
- 보충 매수 시 `max_positions - 현재 보유 종목 수`만큼만 신규 주문을 만든다

### 매도 조건

- 익절: 평균매수가 대비 +3.0%
- 손절: 평균매수가 대비 -5.0%
- 주중 모니터링은 보유 포지션의 평균매수가(`avg_price`) 기준으로 판단
- 금요일: 남은 보유 종목 전량 시장가 매도

---

## 폴더 구조

```text
Weekly_bot/
  main.py
  requirements.txt
  .env.example
  config/
    settings.yaml
    strategy.yaml
  data/
    sample_market_snapshot.csv
  logs/
    .gitkeep
  scripts/
    run_monday_scan_buy.ps1
    run_tuesday_top_up_buy.ps1
    run_monitor.ps1
    run_friday_liquidate.ps1
  src/
    bot/
      backtest.py
      config.py
      models.py
      runtime.py
      utils.py
      data/
      execution/
      integrations/
      risk/
      strategy/
  tests/
```

---

## 빠른 실행

```bash
python -m venv .venv
source .venv/Scripts/activate  # Git Bash / Windows
pip install -r requirements.txt
python .\Weekly_bot\main.py scan --data .\Weekly_bot\data\sample_market_snapshot.csv
python .\Weekly_bot\main.py buy --data .\Weekly_bot\data\sample_market_snapshot.csv --cash 1000000
python .\Weekly_bot\main.py monitor --positions .\Weekly_bot\logs\positions.csv --data .\Weekly_bot\data\sample_market_snapshot.csv
python .\Weekly_bot\main.py friday-liquidate --positions .\Weekly_bot\logs\positions.csv
python .\Weekly_bot\main.py backtest --start 2024-01-01 --end 2024-12-31 --cash 10000000 --source auto --log-dir .\Weekly_bot\logs
```

PowerShell에서는 다음처럼 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe .\Weekly_bot\main.py scan --data .\Weekly_bot\data\sample_market_snapshot.csv
```

화요일 보충 매수는 별도 전략이 아니라 같은 `buy` 명령을 한 번 더 실행하는 방식입니다. 이미 보유 중인 종목은 제외하고, 빈 슬롯이 남아 있을 때만 신규 매수 주문이 생성됩니다.

```powershell
.\Weekly_bot\scripts\run_tuesday_top_up_buy.ps1
```

## Backtest

`backtest` 명령은 KOSPI200 현재 구성 종목을 기준으로 과거 일봉 데이터를 자동 수집해서 주간 전략을 간이 재현합니다.

- 기본 데이터 소스는 `auto`이며, `FinanceDataReader`를 우선 사용하고 실패 시 `yfinance` 가격 데이터를 시도합니다.
- 기본 백테스트는 전주 금요일 신호 생성, 다음 거래일인 월요일 진입, 주중 익절·손절 판정, 금요일 강제청산 흐름으로 근사합니다.
- 같은 날 익절가와 손절가가 모두 닿으면 기본적으로 `익절 75% / 손절 25%` 비율이 되도록 종목/날짜 기준으로 결정합니다.
- `--approx-monday-10am` 옵션을 켜면 월요일 10:00 진입을 더 가깝게 근사합니다.
- `--monday-approx-price-mode open|mid|weighted`로 월요일 근사 가격을 고를 수 있습니다.
- 과거 호가 데이터가 없기 때문에 스프레드는 1틱 차이 수준으로 단순화합니다.

예시:

```powershell
.\.venv\Scripts\python.exe .\Weekly_bot\main.py backtest --start 2024-01-01 --end 2024-12-31 --cash 10000000 --source auto --signal-weekday friday --entry-offset-days 1 --approx-monday-10am --monday-approx-price-mode mid --monday-approx-max-gap-pct 2.0 --collision-tp-ratio 0.75 --buy-slippage-bps 5 --sell-slippage-bps 5 --log-dir .\Weekly_bot\logs
```

생성 파일:

- `logs\backtests\run_YYYYMMDD_HHMMSS\summary.csv`
- `logs\backtests\run_YYYYMMDD_HHMMSS\trades.csv`
- `logs\backtests\run_YYYYMMDD_HHMMSS\weekly.csv`
- `logs\backtests\run_YYYYMMDD_HHMMSS\monthly.csv`
- `logs\backtests\run_YYYYMMDD_HHMMSS\run_manifest.json`
- `logs\backtests\run_YYYYMMDD_HHMMSS\config_snapshot.yaml`

`--run-name` 옵션을 주면 실행별 폴더 이름을 직접 지정할 수 있습니다. 각 실행 폴더에는 전략 설정값과 백테스트 실행 옵션이 함께 저장되므로, 설정값을 바꿔가며 실험한 이력을 나중에 추적할 수 있습니다.

## 현재 기준값

현재 백테스트 기준 기본값은 다음과 같습니다.

- `change_pct`: `-7.0% ~ -2.0%`
- `min_turnover_krw`: `1,000,000,000`
- `envelope_lower_pct`: `2.6%`
- `take_profit_pct`: `3.0%`
- `stop_loss_pct`: `-5.0%`
- `min_volume`: 비활성화 (`0`)
- `max_spread_ticks`: 비활성화 (`0`)

현재 백테스트 가정:

- 신호일: 이전 금요일 기준
- 진입: 다음 거래일 기준, 필요 시 월요일 10:00 근사 우선 적용
- 청산: 장중 TP/SL 터치 기준, 남은 포지션은 금요일 강제청산

---

## 실제 키움 연동 관련 메모

현재 `src/bot/execution/kiwoom_real.py`를 통해 실거래 주문 경로가 연결되어 있습니다. 다만 실거래 확대 전에는 아래 순서로 검증을 유지하는 것이 안전합니다.

1. CSV 데이터 기반 dry-run
2. 실시간 시세 조회 연결
3. 소액 또는 주문 없는 실시간 검증
4. 제한된 종목 수와 금액으로 실거래
5. 전략 고정 후 4주 이상 로그 축적

---

## 로그 정책

이 봇은 전략 검증을 위해 다음 로그를 남기도록 설계되어 있습니다.

- 후보 종목 로그: `logs/candidates.csv`
- 주문 로그: `logs/orders.csv`
- 보유 포지션 로그: `logs/positions.csv`
- 매도 판단 로그: `logs/exit_decisions.csv`

초기 검증 기간에는 조건을 너무 자주 바꾸지 않는 것을 권장합니다. 조건을 자주 바꾸면 전략 자체의 유효성 검증이 어려워집니다.

## Real Mode Examples

```powershell
.\Weekly_bot\scripts\run_monday_scan_buy.ps1
.\Weekly_bot\scripts\run_tuesday_top_up_buy.ps1
.\Weekly_bot\scripts\run_monitor.ps1
.\Weekly_bot\scripts\run_friday_liquidate.ps1
```
