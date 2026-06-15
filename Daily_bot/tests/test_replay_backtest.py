import sqlite3
from pathlib import Path

from Daily_bot.backtest.replay_market_traces import (
    load_selected_signals,
    pick_entries,
    run_backtest,
    write_backtest_reports,
)


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
        stop_loss_percent=6.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=1,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
    )

    assert len(trades) == 2
    assert trades[0].ticker == "BBB"
    assert trades[1].ticker == "AAA"


def test_run_backtest_replays_dynamic_reentry_with_slot_limit(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
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
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 103, 3.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 103, 102, 104, 103, 3.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "scan_candidate", "CCC", 0, "", 100, 100, 99, 101, 102, 2.0, 0.2, "{}", "2026-06-02 09:32:00"),
            ("2026-06-02", "watchlist", "CCC", 0, "", 100, 102, 101, 103, 102, 2.0, 0.2, "{}", "2026-06-02 09:33:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=1,
        stop_loss_percent=1.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=1,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
    )

    assert len(trades) == 2
    assert [trade.ticker for trade in trades] == ["BBB", "CCC"]
    assert all(trade.exit_reason == "take_profit" for trade in trades)


def test_run_backtest_respects_session_capital_slot_budget(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
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
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 101, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=5,
        stop_loss_percent=1.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=500,
        min_slot_count=1,
        max_slot_count=5,
        slot_budget_unit_krw=500,
        max_budget_per_stock_krw=500,
    )

    assert len(trades) == 2
    assert trades[0].ticker == "AAA"
    assert trades[0].entry_time == "2026-06-02 09:30:00"
    assert trades[1].ticker == "BBB"
    assert trades[1].entry_time == "2026-06-02 09:31:00"


def test_run_backtest_respects_configurable_stop_buy_time(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 101, 1.0, 0.2, "{}", "2026-06-02 11:29:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 101, 1.0, 0.2, "{}", "2026-06-02 11:31:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 15:00:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 15:00:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=5,
        stop_loss_percent=10.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=5,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
        start_buy_time="09:30",
        stop_buy_time="11:30",
        force_sell_time="15:00",
    )

    assert [trade.ticker for trade in trades] == ["AAA"]


def test_run_backtest_uses_force_sell_time_before_last_trace(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 105, 5.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 99, 98, 100, 105, 5.0, 0.2, "{}", "2026-06-02 15:00:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 95, 94, 96, 105, 5.0, 0.2, "{}", "2026-06-02 15:10:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=5,
        stop_loss_percent=10.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=5,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
        force_sell_time="15:00",
    )

    assert len(trades) == 1
    assert trades[0].exit_reason == "force_exit_time"
    assert trades[0].exit_time == "2026-06-02 15:00:00"
    assert trades[0].exit_price == 99


def test_run_backtest_applies_spread_weighted_expected_return_filter(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 102, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 102, 101, 103, 102, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 101, 0.55, 0.5, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 101, 100, 102, 101, 0.55, 0.5, "{}", "2026-06-02 09:31:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.3,
        max_spread_percent=0.7,
        top_n_per_day=5,
        stop_loss_percent=6.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=5,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
        spread_expected_return_multiplier=1.2,
    )

    assert [trade.ticker for trade in trades] == ["AAA"]


def test_run_backtest_applies_orderbook_ask_depth_ratio_when_trace_data_is_available(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
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
            ask_depth_5_amount_krw INTEGER,
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
         expect_price, expect_revenue_percent, spread_percent, ask_depth_5_amount_krw, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 102, 1.0, 0.2, 200, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 102, 101, 103, 102, 1.0, 0.2, 200, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 103, 2.0, 0.2, 2000, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 102, 101, 103, 103, 2.0, 0.2, 2000, "{}", "2026-06-02 09:31:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=5,
        stop_loss_percent=6.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=500,
        min_slot_count=1,
        max_slot_count=5,
        slot_budget_unit_krw=500,
        max_budget_per_stock_krw=500,
        max_orderbook_ask_depth_ratio=0.30,
        missing_ask_depth_policy="skip",
    )

    assert [trade.ticker for trade in trades] == ["BBB"]


def test_write_backtest_reports_emits_daily_rev_and_daily_audit_csv(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
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
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-03", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 99, 1.0, 0.2, "{}", "2026-06-03 09:30:00"),
            ("2026-06-03", "watchlist", "AAA", 0, "", 100, 99, 98, 100, 99, 1.0, 0.2, "{}", "2026-06-03 09:31:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=5,
        stop_loss_percent=6.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=1,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
    )

    out_path = tmp_path / "backtest_replay.csv"
    report_paths = write_backtest_reports(
        out_path=out_path,
        trades=trades,
        session_capital_by_day=None,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=1,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
        max_position_count=0,
        target_budget_ratio_per_stock=0.0,
    )

    daily_rev_text = report_paths["daily_rev"].read_text(encoding="utf-8-sig")
    daily_audit_text = report_paths["daily_audit"].read_text(encoding="utf-8-sig")

    assert "2026-06-02" in daily_rev_text
    assert "2026-06-03" in daily_rev_text
    assert "starting_capital_krw" in daily_rev_text
    assert "trade_date" in daily_audit_text
    assert "backtest_replay" in daily_audit_text
    assert "BUY-2026-06-03-AAA-20260603T093000" in daily_audit_text
    assert ",10000,1000000,100.0,0,0," in daily_audit_text
    assert ",20000,2000000,100.0,0,0," not in daily_audit_text
