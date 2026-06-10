# Codex Handoff - Daily Bot

이 문서는 다음 작업자가 현재 `Daily_bot`을 빠르게 이해하고, 코드 수정 시 무엇을 보존해야 하는지 파악하도록 돕는 운영용 인수인계 문서입니다.

## 1. 현재 봇의 정체성

이 봇은 더 이상 스켈레톤이나 목업 단계가 아닙니다.

- 키움 REST API를 실거래에 사용합니다.
- 장중 체결은 `ka10076` 기반으로 기록합니다.
- 장 마감 후 `15:15`에 브로커 체결과 로컬 원장을 재대조합니다.
- 기록은 "전략 기대수익률은 낮게 보지 않되, 숫자는 매우 엄격하게 검증한다"는 운영 원칙을 따릅니다.

## 2. 운영 타임라인

```text
08:55 ~ 09:30  유니버스 워밍업
09:30 ~ 13:00  신규 매수 가능
13:00 ~ 15:00  신규 매수 중단, 보유/주문만 관리
15:00          미체결 정리 + 강제청산
15:15          브로커 체결 일괄 대조
15:20          종료
```

관련 설정은 [settings.yaml](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/config/settings.yaml:1)에 있습니다.

## 3. 핵심 파일

```text
Daily_bot/
  main.py                    메인 루프, 장중 흐름, 15:15 대조
  broker/kiwoom_client.py    키움 REST 래퍼, 체결조회/계좌조회
  storage/db.py              SQLite 원장, fills/orders 저장
  storage/audit_csv.py       감사용 CSV 누적/재생성
  risk/force_sell.py         15:00 이후 강제청산
  risk/stop_loss.py          손절 감시/실행
  config/settings.yaml       운영 시간과 전략 파라미터
```

## 4. 절대 보존해야 할 동작

다음은 수정 시 깨지면 안 되는 핵심 동작입니다.

1. 매수 체결 전에는 매도 주문을 넣지 않는다.
2. 매수 체결 후에는 목표 지정가 매도 주문을 즉시 넣는다.
3. `15:00` 이후에는 신규 매수를 하지 않는다.
4. `15:15` 이후에는 브로커 체결 전체를 다시 조회해 로컬 체결 원장을 정정한다.
5. `trade_fills_audit.csv`는 append-only처럼 보이더라도, 마감 대조 후에는 DB 기준으로 재생성할 수 있어야 한다.
6. 장중 손익은 MTS가 더 신뢰도가 높고, 마감 후 기록은 브로커 대조 후 로컬 원장이 더 신뢰도가 높다.

## 5. 체결기록 구조

### 장중

- 주문별 체결 확인은 `kiwoom_client.get_order_fill()`을 통해 수행합니다.
- 이 함수는 `ka10076` 당일 체결목록을 조회한 뒤 로컬에서 주문번호를 매칭합니다.
- `ord_no`를 직접 필터값처럼 쓰지 않는 이유:
  `ka10076.ord_no`는 정확한 주문번호 조회 필드가 아니라 과거 조회 커서 성격이기 때문입니다.

### 장 마감 후

`main.reconcile_broker_fills()`가 아래 작업을 수행합니다.

1. 브로커 체결 전체 조회
2. 주문번호/매수-매도 기준으로 `fills` 원장 교체
3. `fills_YYYYMMDD.csv` 재생성
4. `trade_fills_audit.csv` 재생성

이 흐름은 [main.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/main.py:292), [db.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/storage/db.py:411), [audit_csv.py](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/storage/audit_csv.py:232)에 구현돼 있습니다.

## 6. 손익 해석 기준

### 장중

- 체결 누락/지연 가능성이 있으므로 `MTS 우선`

### 마감 후

- 거래 복원: `fills` 원장 우선
- 기간 실현손익: `ka10074`
- 시작자산 대비 수익률: `kt00002`
- 현재 보유 포함 총손익: `ka10074 + kt00005`

### 감사용 CSV 주의사항

`trade_fills_audit.csv`는 종목별 누적 상태를 담습니다.

- `SELL` 행을 전부 더해서 총손익을 계산하면 안 됩니다.
- 증빙/검토 용도로는 유용하지만 총손익 집계 원본은 아닙니다.

## 7. 최근 반영된 중요한 변경점

현재 문서 기준으로 이미 반영된 내용:

- `15:15` 브로커 체결 대조 추가
- `15:20` 종료 추가
- `ka10076` 연속조회 및 주문번호 로컬 매칭
- 체결가를 실체결가 기준으로 저장
- 분할체결 시 가중평균 체결가 저장
- 브로커 원본 수수료/세금 사용
- 마감 후 `fills_YYYYMMDD.csv`와 `trade_fills_audit.csv` 재생성

## 8. 앞으로 수정할 때의 기본 태도

- 전략 잠재수익률을 낮게 가정하지 말 것
- 대신 기록과 회계 숫자는 항상 의심하고 교차검증할 것
- "기록이 맞는지"와 "전략이 좋은지"를 같은 문제로 섞지 말 것

실무적으로는 아래 순서가 안전합니다.

1. 브로커 원본 확인
2. DB `fills` 확인
3. CSV 재생성 여부 확인
4. 그 다음에 전략/성과 해석
