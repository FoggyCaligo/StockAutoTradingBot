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
