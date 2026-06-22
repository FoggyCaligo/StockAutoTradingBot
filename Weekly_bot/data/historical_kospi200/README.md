Historical KOSPI200 membership data for `Weekly_bot`.

Expected file:

- `membership.csv`

Expected columns:

- `code`: six-digit stock code
- `effective_from`: inclusive start date in `YYYY-MM-DD`
- `effective_to`: inclusive end date in `YYYY-MM-DD`
- `source`: optional source label
- `note`: optional note

Notes:

- The backtest engine reads `membership.csv` and treats each row as an active membership interval.
- For a given date, all rows with `effective_from <= date <= effective_to` are considered KOSPI200 members.
- This directory is intentionally separate from `kospi200_latest.csv` because the latest snapshot cannot be reused as a historical universe.
- As of June 22, 2026, a reliable public source for the full 2020-2024 historical KOSPI200 membership set has not yet been imported here.
