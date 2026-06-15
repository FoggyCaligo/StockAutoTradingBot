from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from Daily_bot.storage.db import Recorder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Daily_bot/logs/daily_rev.csv from historical fills.")
    parser.add_argument("--db", default="Daily_bot/bot.sqlite3")
    parser.add_argument("--log-dir", default="Daily_bot/logs")
    return parser.parse_args()


def load_session_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT substr(filled_at, 1, 10) AS session_date
        FROM fills
        WHERE filled_at IS NOT NULL
          AND filled_at != ''
        ORDER BY session_date
        """
    ).fetchall()
    return [str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()]


def resolve_starting_capital(conn: sqlite3.Connection, session_date: str) -> int:
    row = conn.execute(
        """
        SELECT adjusted_account_value, account_value, cash
        FROM account_traces
        WHERE session_date = ?
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        (session_date,),
    ).fetchone()
    if row is None:
        return 0

    for key in ("adjusted_account_value", "account_value", "cash"):
        try:
            value = int(row[key] or 0)
        except (TypeError, ValueError, IndexError):
            value = 0
        if value > 0:
            return value
    return 0


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    log_dir = Path(args.log_dir)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    session_dates = load_session_dates(conn)
    capital_by_day = {session_date: resolve_starting_capital(conn, session_date) for session_date in session_dates}
    conn.close()

    recorder = Recorder(db_path, log_dir=log_dir)
    try:
        for session_date in session_dates:
            starting_capital = capital_by_day.get(session_date, 0)
            if starting_capital <= 0:
                print(f"skip {session_date}: starting capital could not be resolved")
                continue
            recorder.write_daily_revenue_summary(session_date, starting_capital_krw=starting_capital)
            print(f"wrote {session_date}: starting_capital_krw={starting_capital}")
    finally:
        recorder.conn.close()


if __name__ == "__main__":
    main()
