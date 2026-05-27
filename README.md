# Kiwoom Hoga Snapshot Bot

KOSPI200 후보군을 대상으로 20호가 스냅샷을 1회 조회하고, 호가 상쇄 알고리즘으로 예상가/예상수익률을 계산한 뒤 상위 후보를 매수하는 자동매매 봇의 1차 골격입니다.

## 전략 개요

1. 계좌에 보유 주식과 미체결 주문이 없을 때만 신규 스캔을 시작합니다.
2. KOSPI200 종목 중 가격, 시가총액, 거래대금, 차트 우상향 조건을 만족하는 후보군을 만듭니다.
3. 후보별 20호가 스냅샷을 REST API로 1회 조회합니다.
4. 호가 상쇄 알고리즘으로 예상가를 계산합니다.
5. 예상수익률 상위 10%를 뽑고, 절대 수익률/스프레드/예산 필터를 통과한 종목만 매수합니다.
6. 매수 체결 즉시 `예상가 - 1틱`에 지정가 매도 주문을 넣습니다.
7. 13:00까지 미체결/보유 종목이 있으면 기존 주문을 취소하고 시장가로 강제매도합니다.

## 현재 상태

이 압축 파일은 큰 틀만 잡은 스캐폴딩입니다. 키움 REST API의 실제 endpoint, header, body, 응답 필드명은 공식 문서에 맞춰 `broker/kiwoom_client.py`에 채워야 합니다.

## 실행 준비

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

`config/settings.yaml`에서 전략 파라미터를 조정합니다.

## 실행

```bash
python main.py --dry-run
```

`--dry-run`은 주문을 실제로 넣지 않고 로그만 남기는 모드입니다.

## 디렉터리 구조

```text
broker/       키움 REST API 인증, 조회, 주문 래퍼
strategy/     후보군 생성, 호가 예측, 랭킹/신호 생성
risk/         예산, 수량, 강제청산, 리스크 가드
storage/      SQLite 기록 저장
config/       설정 파일
scripts/      보조 스크립트
docs/         설계 메모
tests/        테스트 골격
```

## 주의

실전 투입 전에는 최소 1~2주간 dry-run/가상매매 로그를 쌓아야 합니다. 자동매매는 오작동 시 실제 손실이 발생할 수 있으므로, 실거래 전 주문/체결/잔고 동기화와 강제청산 로직을 반드시 검증해야 합니다.




## 검증

1단계: 기능 검증
- 실거래 주문 성공
- 체결 확인 성공
- 미체결 취소 성공
- remaining_cash 정상 복구
- 13시 청산 정상 작동
- 로그/거래기록 누락 없음

2단계: 소액 성능 검증
- 최소 3~4주
- 최소 20회 이상 거래
- 수수료/세금 반영 후 플러스
- 특정 1~2종목 운빨이 아니라 반복적으로 수익 발생
- 큰 손실 1번으로 전체 수익이 날아가지 않음

3단계: 증액
- 한 번에 크게 넣지 않고
- 기존 금액의 1.5배 또는 2배 이하로만 증액
- 증액 후 다시 2~4주 관찰


## 2026-05-28 확인할 것
최소 확인 : 09:05 이후
Get-ChildItem .\logs | Sort-Object LastWriteTime -Descending | Select-Object -First 5


09:05 이후 작업 스케줄러 확인
schtasks /Query /TN "StockAutoTradingBot-Real" /V /FO LIST
Last Run Time가 오늘로 바뀌고, Task To Run이 그대로 run_real.ps1이면 자동 실행은 탄 겁니다.

로그 파일 생성 확인
Get-ChildItem .\logs | Sort-Object LastWriteTime -Descending | Select-Object -First 5
run_real_YYYYMMDD_HHMMSS.log가 09:05 근처 시각으로 생기면 실제 프로세스가 시작된 겁니다.

로그 실시간 보기
Get-Content .\logs\<방금생긴로그파일명> -Wait
여기서 인증, 후보군 스캔, 주문, 강제청산 관련 출력이 이어지면 정상 동작 중입니다.

DB 기록 증가 확인
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('bot.sqlite3'); print('snapshots', c.execute('select count(*) from hoga_snapshots').fetchone()[0]); print('signals', c.execute('select count(*) from signals').fetchone()[0]); print('orders', c.execute('select count(*) from orders').fetchone()[0])"
숫자가 늘면 실제 로직이 진행된 겁니다.

13:00 이후 종료 확인
로그에 Force sell completed. Stop trading for today. 가 찍히면 당일 사이클이 끝난 겁니다.
추가로, 내일은 실거래라서 09:05 전에 PC가 켜져 있고 로그인 세션도 유지되어 있어야 합니다.


## 안전 장치
장치	역할
코스피200	   : 유동성/상장 안정성 필터
예상수익률   : 상위	기회 선별
예상가 -1틱  : 	익절 체결 가능성 확보
-2% 손절	    : 실패 시 손실 제한
13시 강제청산: 	장중 미체결 리스크 제거