# Daily Bot Strategy Design

이 문서는 전략 설계 문서의 짧은 안내판이다. 현재 상세 전략 설명과 실제 동작 기준은 [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md) 로 통합했다.

## 핵심 요약

- 유니버스: 당일 조회된 전체 KOSPI 중 시가총액/거래대금 필터 통과 종목
- 스캔 주기: 60초
- 신호 원천: 20호가 기반 예측가와 기대수익률
- 핵심 필터: 기대수익률 문턱과 fallback
- 자본 계획: 자본 기반 슬롯 수 계산 + 배치당 신규 진입 상한
- 기본 배치 정책: 일부 슬롯이 비어도 전체 배치가 비기 전까지 재진입하지 않음
- 청산: 목표가 익절 우선, `-4.5%` 손절, `15:00` 강제청산

## 읽는 순서

1. [CURRENT_DAILY_SETTINGS.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CURRENT_DAILY_SETTINGS.md)
2. [DAILY_BOT_LOGIC_REFERENCE.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/DAILY_BOT_LOGIC_REFERENCE.md)
3. [CODEX_HANDOFF.md](/C:/Users/bigla/OneDrive/Documents/GIT/StockAutoTradingBot/Daily_bot/docs/CODEX_HANDOFF.md)
