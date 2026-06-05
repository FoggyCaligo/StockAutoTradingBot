import sqlite3
from pathlib import Path

from Daily_bot.reporting.performance import summarize_realized_performance


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_order_id TEXT,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price INTEGER NOT NULL,
            filled_at TEXT NOT NULL,
            source TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


def test_summarize_realized_performance_uses_fills_only(tmp_path):
    db_path = tmp_path / "fills.sqlite3"
    _init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.executemany(
        """
        INSERT INTO fills (broker_order_id, ticker, side, quantity, price, filled_at, source, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("B1", "005930", "BUY", 10, 10000, "2026-06-05T09:31:00+09:00", "poll", "{}", "2026-06-05 00:31:00"),
            ("S1", "005930", "SELL", 10, 10100, "2026-06-05T09:32:00+09:00", "poll", "{}", "2026-06-05 00:32:00"),
            ("B2", "000660", "BUY", 5, 20000, "2026-06-05T09:33:00+09:00", "poll", "{}", "2026-06-05 00:33:00"),
        ],
    )
    conn.commit()
    conn.close()

    summary = summarize_realized_performance(str(db_path), session_date="2026-06-05")

    assert summary.trade_count == 1
    assert summary.gross_pnl_krw == 1000
    assert summary.wins == 1
    assert summary.losses == 0
    assert summary.open_buy_count == 5
    assert summary.open_buy_cost_krw == 100000
    assert summary.trades[0].ticker == "005930"


def test_summarize_realized_performance_matches_partial_sell_fifo(tmp_path):
    db_path = tmp_path / "fills_fifo.sqlite3"
    _init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.executemany(
        """
        INSERT INTO fills (broker_order_id, ticker, side, quantity, price, filled_at, source, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("B1", "251270", "BUY", 4, 42000, "2026-06-05T09:31:00+09:00", "poll", "{}", "2026-06-05 00:31:00"),
            ("B2", "251270", "BUY", 4, 42150, "2026-06-05T09:31:10+09:00", "poll", "{}", "2026-06-05 00:31:10"),
            ("S1", "251270", "SELL", 6, 42450, "2026-06-05T09:32:00+09:00", "poll", "{}", "2026-06-05 00:32:00"),
        ],
    )
    conn.commit()
    conn.close()

    summary = summarize_realized_performance(str(db_path), session_date="2026-06-05")

    assert summary.trade_count == 2
    assert summary.gross_pnl_krw == ((42450 - 42000) * 4) + ((42450 - 42150) * 2)
    assert summary.open_buy_count == 2
    assert summary.open_buy_cost_krw == 84300
