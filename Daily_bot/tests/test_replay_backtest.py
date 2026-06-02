import sqlite3
from pathlib import Path

from Daily_bot.backtest.replay_market_traces import load_selected_signals, pick_entries, run_backtest


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE market_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            phase TEXT NOT NULL,
            ticker TEXT NOT NULL,
            selected INTEGER DEFAULT 0,
            reason TEXT,
            price INTEGER,
            current_price INTEGER,
            best_bid INTEGER,
            best_ask INTEGER,
            expect_price INTEGER,
            expect_revenue_percent REAL,
            spread_percent REAL,
            raw_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            created_at TEXT NOT NULL,
            price INTEGER,
            expect_price INTEGER,
            expect_revenue_percent REAL,
            spread_percent REAL,
            selected INTEGER DEFAULT 0
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO market_traces
        (session_date, phase, ticker, selected, reason, price, current_price, best_bid, best_ask,
         expect_price, expect_revenue_percent, spread_percent, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 101, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 1, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 102, 2.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 102, 101, 103, 102, 2.0, 0.2, "{}", "2026-06-02 09:31:00"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO signals
        (ticker, created_at, price, expect_price, expect_revenue_percent, spread_percent, selected)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("AAA", "2026-06-02 09:30:30", 100, 101, 1.0, 0.2, 1),
        ],
    )
    conn.commit()
    conn.close()


def test_load_selected_signals_reads_selected_rows(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    _create_db(db_path)

    rows = load_selected_signals(db_path)

    assert len(rows) == 1
    assert rows[0].ticker == "AAA"


def test_pick_entries_prefers_selected_signals_when_available(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    _create_db(db_path)

    from Daily_bot.backtest.replay_market_traces import group_by_session_and_ticker, load_traces

    grouped = group_by_session_and_ticker(load_traces(db_path))
    selected = load_selected_signals(db_path)

    result = pick_entries(grouped, 0.25, 0.7, 3, selected_signals=selected)

    assert list(result.keys()) == ["2026-06-02"]
    assert [row.ticker for row in result["2026-06-02"]] == ["AAA"]


def test_run_backtest_can_ignore_selected_signals_and_fall_back_to_top_ranked(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    _create_db(db_path)

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=1,
        take_profit_percent=0.25,
        stop_loss_percent=6.0,
        use_selected_signals=False,
    )

    assert len(trades) == 1
    assert trades[0].ticker == "BBB"
