# Daily Bot Strategy Design

이 문서는 현재 전략의 설계 철학과 큰 구조만 짧게 설명한다. 실제 활성 로직과 세부 재구성 설명은 [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)를 우선한다.

## 전략 구조

현재 데일리 봇의 중심 철학은 아래 다섯 가지다.

- 장중 단기 균형가격을 호가에서 직접 추정한다.
- 기대수익률이 충분히 큰 종목만 진입한다.
- 기대수익률이 계산된 후보 중에서도 상위 일부만 남긴다.
- 자본 배분은 고정 종목 수가 아니라 슬롯 구조로 관리한다.
- 손절보다 목표가 체결과 장마감 정리를 더 중심에 둔다.

## 현재 설계 포인트

### 1. 유니버스

- KOSPI 전체를 당일 다시 조회한다.
- 시가총액과 거래대금 필터로 너무 얇은 종목을 제거한다.

### 2. 신호 계산

- 호가 잔량 상쇄 모델로 `expect_price`를 계산한다.
- 최근 변경으로 매수/매도 양쪽 호가잔량에 강한 대칭 선형 감쇠를 건다.
- 현재 감쇠 기본값은 `1.0 -> 0.0`이다.

### 3. 진입 기준

- 기대수익률 `0.7%` 이상만 진입 후보로 본다.
- 그 후보 중 상위 `25%`만 다음 선택 단계로 넘긴다.
- fallback은 현재 꺼져 있다.
- 전일 `10.0%` 이상 오른 과열 종목은 제외한다.

### 4. 포지션 운영

- 스캔당 신규 매수는 최대 3개
- 총 보유는 자본 기반 슬롯 수와 하드 상한 10개로 통제
- 빈 슬롯이 생기면 다음 스캔에서 재진입 허용

### 5. 청산 철학

- 매수 직후 목표가 매도 우선
- 장중 손절은 현재 비활성화
- 남은 포지션은 `15:00` 전량 강제청산

## 현재 전략 해석

현재 전략은 "약한 후보를 많이 사는 전략"이 아니라, `0.7` 이상의 비교적 강한 후보만 고르고, 그 안에서 슬롯을 계속 회전시키는 구조다. 최근 변경의 핵심은 먼 호가잔량의 과대평가를 줄이기 위해 감쇠를 넣었고, 장 후반 반등 가능성을 고려해 장중 손절을 껐다는 점이다.

## 함께 봐야 하는 문서

1. [CURRENT_DAILY_SETTINGS.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CURRENT_DAILY_SETTINGS.md)
2. [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)
3. [CODEX_HANDOFF.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CODEX_HANDOFF.md)
