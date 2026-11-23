# 데일리봇 가상 시장 6개월 자료

실제 생성한 6개월 합성 시장을 일반 Git의 10MiB 이하 조각 파일로 보관한다. 원본 ZIP 15개, 총 5,505,923,673바이트를 그대로 분할한 것이며 재생성한 다른 시장이 아니다. 조각별 SHA-256과 Git blob SHA-1, 원본 ZIP SHA-256을 `parts_manifest.json`에 기록했다. Git LFS는 사용하지 않는다.

업로드 완료 여부는 `dataset_status.json`을 확인한다. `complete: false`인 동안에는 일부 조각이 아직 반영 중이다. `complete: true`일 때 모든 조각을 원격 Git tree의 blob ID와 대조한 상태다.

## 자료 구성

| 항목 | 값 |
|---|---|
| 구간 | 2026-11-23부터 2027-05-22까지 6개월. 마지막 기록일 2027-05-21 |
| 날짜 | 주말 제외 130일. 공휴일 별도 제외 없음 |
| 종목 | 매일 5~10개, 총 1,019종목일 |
| 기록 | 1초 간격, 23,233,200행 |
| 장중 시간 | 한국 시각 09:00:00 이상 15:20:00 미만 |
| 방향 | 기준선 x=100bp/일, 하루 +1% 상승 방향 |
| 난수 | seed 42, 종목별 독립 난수 스트림 |
| 내용 | 가격·10단계 호가·잔량·틱 체결량·거래대금. 개별 체결 주문번호 목록 제외 |

`parts/`에 실제 데이터 조각이 들어간다. `manifest.json`, `dataset_config.json`, `daily_summary.csv`, `universe.csv`는 전체 자료의 파일 목록·설정·요약이다. 생성 원리와 한계, 재현 및 백테스트 입력 준비 방법은 [생성 자료 설명](GENERATION_NOTES.md)을 참조한다.

## 원본 ZIP 복원

저장소 루트에서 필요한 달을 복원한다. 아래 명령은 Python 표준 라이브러리만 사용한다.

```bash
python Daily_bot/data/fake_market/six_months_20261123_seed42/restore_archives.py --month 2026-11
```

다른 달은 `--month 2027-01`처럼 바꾼다. `--month`를 생략하면 원본 ZIP 15개를 모두 복원한다. 조각의 순서·길이·SHA-256과 합친 원본의 SHA-256을 모두 확인한 뒤 완성된 ZIP으로 이름을 바꾼다. 검증 중 오류가 나면 불완전한 임시 ZIP을 지운다. 기존 ZIP이 원본과 다르면 덮어쓰지 않는다.

복원 파일은 이 폴더의 `reconstructed/`에 생긴다. 원하는 기간의 ZIP들을 같은 폴더에 각각 압축 해제하고 `prepare_replay.py`로 하루 또는 짧은 구간의 DB를 만든다. 기존 러너는 전체 trace를 메모리에 적재하므로 6개월 전체를 한 번에 적재하는 방식은 권하지 않는다.

파일을 합치지 않고 검증만 할 때:

```bash
python Daily_bot/data/fake_market/six_months_20261123_seed42/restore_archives.py --verify-only
```

## 저장 방식

각 원본 ZIP을 최대 10MiB의 `.zip.001`, `.zip.002` 같은 조각으로 나눴다. GitHub의 파일당 한도와 연결 API의 요청 크기 안에 맞춘 것이다. 커밋은 원본 ZIP별로 나누며, 이미 공개한 원본 ZIP의 체크섬은 `SHA256SUMS`와 같다.

일반 Git에 실제 바이트를 보관하므로 전체 clone과 checkout에는 데이터 전체에 해당하는 전송량·디스크 공간이 필요하다. `reconstructed/`는 Git에서 제외한다. 가상 자료의 수익은 실제 시장 수익성을 입증하지 않으며, 모형의 구조적 편향과 기존 replay 체결 한계도 그대로 유지된다.
