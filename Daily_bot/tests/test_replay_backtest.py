import sqlite3
from pathlib import Path

from Daily_bot.backtest import replay_db_builder
from Daily_bot.backtest.replay_market_traces import (
    ActualExitOverride,
    infer_selected_signals_from_fill_audit,
    load_actual_exit_overrides_from_fills,
    load_trend_ok_tickers_by_day,
    load_selected_signals,
    parse_args,
    pick_entries,
    _resolve_stop_loss_price,
    run_backtest,
    write_backtest_reports,
)
from Daily_bot.backtest.replay_db_builder import resolve_replay_db_path


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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 102, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 1, "", 100, 101, 100, 102, 102, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
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
            ("AAA", "2026-06-02 09:30:30", 100, 102, 1.0, 0.2, 1),
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


def test_infer_selected_signals_from_fill_audit_prefers_raw_fill_logs_when_signals_table_is_empty(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE market_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            phase TEXT NOT NULL,
            ticker TEXT NOT NULL,
            scan_cycle_at TEXT,
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
            scan_cycle_at TEXT,
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
        (session_date, phase, ticker, scan_cycle_at, selected, reason, price, current_price, best_bid, best_ask,
         expect_price, expect_revenue_percent, spread_percent, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "AAA", "2026-06-02 09:30:00", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "scan_candidate", "BBB", "2026-06-02 09:30:00", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
        ],
    )
    conn.commit()
    conn.close()

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "fills_20260602.csv").write_text(
        "\n".join(
            [
                "ticker,side,filled_at,price,quantity,order_id,signal_time,position_id",
                "AAA,BUY,2026-06-02T09:30:20,100,1,,,",
            ]
        ),
        encoding="utf-8-sig",
    )
    (logs_dir / "trade_fills_audit_daily.csv").write_text(
        "\n".join(
            [
                "trade_date,filled_at,broker_order_id,ticker,side,quantity,price,amount,estimated_fee,estimated_tax,estimated_total_cost,source,cash,account_value,adjusted_account_value,adjusted_pnl,loss_percent,kospi_change_percent,cum_buy_quantity,cum_buy_amount,avg_buy_price,cum_sell_quantity,cum_sell_amount,realized_pnl_before_costs,estimated_net_realized_pnl,realized_return_percent_before_costs,estimated_net_realized_return_percent,position_status",
                "2026-06-02,2026-06-02T09:30:20,BUY-1,BBB,BUY,1,100,100,0,0,0,broker,1000000,1000000,1000000,0,0,0,1,100,100,0,0,,,,OPEN",
            ]
        ),
        encoding="utf-8-sig",
    )

    rows = infer_selected_signals_from_fill_audit(db_path, logs_dir)

    assert len(rows) == 1
    assert rows[0].ticker == "AAA"
    assert rows[0].created_at == "2026-06-02 09:30:00"


def test_parse_args_defaults_to_live_config_values(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        "\n".join(
            [
                "market:",
                '  start_buy_time: "09:31"',
                '  stop_buy_time: "11:29"',
                '  force_sell_time: "14:59"',
                "trend_filter:",
                "  enabled: true",
                "strategy:",
                "  allow_refill_empty_slots: true",
                "  top_ratio: 0.5",
                "  max_buy_count: 7",
                "  min_expected_return_percent: 0.9",
                "  min_expected_return_fallback_percents: [0.6]",
                "  max_spread_percent: 0.4",
                "  spread_expected_return_multiplier: 1.2",
                "  min_prev_day_change_percent: -1.5",
                "  max_prev_day_change_percent: 12.5",
                "  sell_tick_offset: 2",
                "risk:",
                "  max_position_count: 6",
                "  min_slot_count: 4",
                "  slot_budget_unit_krw: 7000000",
                "  max_slot_count: 8",
                "  target_budget_ratio_per_stock: 0.3",
                "  max_budget_per_stock_krw: 9000000",
                "  max_orderbook_ask_depth_ratio: 0.25",
                "  stop_loss_tick_count: 3",
                "  stop_loss_tick_multiplier: 1.5",
                "  stop_loss_percent: 3.7",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("sys.argv", ["replay_market_traces.py", "--config", str(config_path)])

    args = parse_args()

    assert args.min_expected_return == 0.9
    assert args.fallback_min_expected_returns == [0.6]
    assert args.fallback_min_expected_return == 0.6
    assert args.max_spread == 0.4
    assert args.min_prev_day_change == -1.5
    assert args.max_prev_day_change == 12.5
    assert args.top_ratio == 0.5
    assert args.stop_loss == 3.7
    assert args.stop_loss_tick_count == 3
    assert args.stop_loss_tick_multiplier == 1.5
    assert args.sell_tick_offset == 2
    assert args.start_buy_time == "09:31"
    assert args.stop_buy_time == "11:29"
    assert args.force_sell_time == "14:59"
    assert args.max_orderbook_ask_depth_ratio == 0.25
    assert args.trend_filter_enabled is True
    assert args.min_slot_count == 4
    assert args.max_slot_count == 8
    assert args.slot_budget_unit_krw == 7_000_000
    assert args.max_budget_per_stock_krw == 9_000_000
    assert args.max_position_count == 6
    assert args.max_buy_count == 7
    assert args.target_budget_ratio_per_stock == 0.3
    assert args.use_selected_signals is False
    assert args.use_actual_fill_exits is False
    assert args.allow_refill_empty_slots is True


def test_load_trend_ok_tickers_by_day_reads_daily_reference_logs(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "daily_reference_prices_20260602.csv").write_text(
        "\n".join(
            [
                "session_date,ticker,name,prev_close_price,market_cap,trading_value,trend_ok,source",
                "2026-06-02,AAA,Alpha,100,100000000000,1000000000,True,test",
                "2026-06-02,BBB,Beta,100,100000000000,1000000000,False,test",
            ]
        ),
        encoding="utf-8-sig",
    )

    trend_ok_by_day, covered_days = load_trend_ok_tickers_by_day(logs_dir)

    assert covered_days == {"2026-06-02"}
    assert trend_ok_by_day == {"2026-06-02": {"AAA"}}


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

    assert len(trades) == 1
    assert trades[0].ticker == "BBB"


def test_run_backtest_uses_selected_signals_by_timestamp_not_by_day(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE market_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            phase TEXT NOT NULL,
            ticker TEXT NOT NULL,
            scan_cycle_at TEXT,
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
            scan_cycle_at TEXT,
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
        (session_date, phase, ticker, scan_cycle_at, selected, reason, price, current_price, best_bid, best_ask,
         expect_price, expect_revenue_percent, spread_percent, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "AAA", "2026-06-02 09:30:00", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", "2026-06-02 09:31:00", 0, "", 100, 120, 119, 120, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "scan_candidate", "AAA", "2026-06-02 10:30:00", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 10:30:00"),
            ("2026-06-02", "scan_candidate", "BBB", "2026-06-02 10:30:00", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 10:30:00"),
            ("2026-06-02", "watchlist", "BBB", "2026-06-02 10:31:00", 0, "", 100, 120, 119, 120, 120, 1.0, 0.2, "{}", "2026-06-02 10:31:00"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO signals
        (ticker, created_at, scan_cycle_at, price, expect_price, expect_revenue_percent, spread_percent, selected)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("AAA", "2026-06-02 09:30:00", "2026-06-02 09:30:00", 100, 120, 1.0, 0.2, 1),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=1,
        stop_loss_percent=6.0,
        use_selected_signals=True,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=1,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
    )

    assert len(trades) == 1
    assert trades[0].ticker == "AAA"


def test_load_actual_exit_overrides_from_fills_aggregates_partial_sells(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "fills_20260602.csv").write_text(
        "\n".join(
            [
                "ticker,side,filled_at,price,quantity,order_id,signal_time,position_id",
                "AAA,BUY,2026-06-02T09:30:10,100,10,,,",
                "AAA,SELL,2026-06-02T09:31:00,101,4,,,",
                "AAA,SELL,2026-06-02T09:32:00,99,6,,,",
            ]
        ),
        encoding="utf-8-sig",
    )

    overrides = load_actual_exit_overrides_from_fills(logs_dir)

    rows = overrides[("2026-06-02", "AAA")]
    assert len(rows) == 1
    assert rows[0] == ActualExitOverride(
        session_date="2026-06-02",
        ticker="AAA",
        buy_filled_at="2026-06-02 09:30:10",
        entry_price=100,
        quantity=10,
        final_exit_time="2026-06-02 09:32:00",
        weighted_exit_price=100,
    )


def test_run_backtest_uses_fallback_expected_return_when_flat_batch_has_no_primary_candidates(tmp_path):
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 102, 0.5, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 102, 0.5, 0.2, "{}", "2026-06-02 09:31:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.6,
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
        fallback_min_expected_return_percents=[0.4],
    )

    assert len(trades) == 1
    assert trades[0].ticker == "AAA"


def test_run_backtest_applies_trend_filter_from_daily_reference_membership(tmp_path):
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
        trend_filter_enabled=True,
        trend_ok_tickers_by_day={"2026-06-02": {"AAA"}},
        trend_filter_days={"2026-06-02"},
    )

    assert len(trades) == 1
    assert trades[0].ticker == "AAA"


def test_run_backtest_can_filter_candidates_by_prev_close_based_jump(tmp_path):
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
            prev_close_price INTEGER,
            current_price INTEGER,
            best_bid INTEGER,
            best_ask INTEGER,
            expect_price INTEGER,
            expect_revenue_percent REAL,
            spread_percent REAL,
            prev_day_change_percent REAL,
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
        (session_date, phase, ticker, selected, reason, price, prev_close_price, current_price, best_bid, best_ask,
         expect_price, expect_revenue_percent, spread_percent, prev_day_change_percent, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 111, 110, 112, 113, 1.8, 0.2, 11.0, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 108, 107, 109, 110, 1.5, 0.2, 8.0, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 100, 110, 109, 111, 110, 1.5, 0.2, 10.0, "{}", "2026-06-02 09:31:00"),
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
        max_prev_day_change_percent=10.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=1,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
    )

    assert len(trades) == 1
    assert trades[0].ticker == "BBB"


def test_resolve_stop_loss_price_uses_dynamic_tick_distance_before_percent_fallback():
    assert _resolve_stop_loss_price(
        entry_price=10_000,
        expect_price=10_200,
        stop_loss_percent=6.0,
        stop_loss_tick_count=0,
        stop_loss_tick_multiplier=2.0,
    ) == 9_920.0


def test_resolve_stop_loss_price_uses_minimum_tick_count_when_dynamic_distance_is_smaller():
    assert _resolve_stop_loss_price(
        entry_price=10_000,
        expect_price=10_200,
        stop_loss_percent=6.0,
        stop_loss_tick_count=5,
        stop_loss_tick_multiplier=1.0,
    ) == 9_950.0


def test_resolve_stop_loss_price_uses_larger_of_minimum_ticks_and_expected_distance():
    assert _resolve_stop_loss_price(
        entry_price=10_000,
        expect_price=10_300,
        stop_loss_percent=6.0,
        stop_loss_tick_count=5,
        stop_loss_tick_multiplier=1.0,
    ) == 9_940.0


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


def test_run_backtest_limits_each_scan_batch_to_configured_max_buy_count(tmp_path):
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
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 102, 2.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "scan_candidate", "CCC", 0, "", 100, 100, 99, 101, 103, 3.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "scan_candidate", "DDD", 0, "", 100, 100, 99, 101, 104, 4.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 102, 101, 103, 102, 2.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "CCC", 0, "", 100, 103, 102, 104, 103, 3.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "DDD", 0, "", 100, 104, 103, 105, 104, 4.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=0,
        stop_loss_percent=1.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=4,
        max_slot_count=4,
        slot_budget_unit_krw=250_000,
        max_budget_per_stock_krw=250_000,
        max_position_count=4,
        max_buy_count=3,
    )

    assert len(trades) == 3
    assert [trade.ticker for trade in trades] == ["DDD", "CCC", "BBB"]
    assert all(trade.exit_reason == "take_profit" for trade in trades)


def test_run_backtest_groups_candidates_by_scan_cycle_time(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE market_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            phase TEXT NOT NULL,
            ticker TEXT NOT NULL,
            scan_cycle_at TEXT,
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
            scan_cycle_at TEXT,
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
        (session_date, phase, ticker, scan_cycle_at, selected, reason, price, current_price, best_bid, best_ask,
         expect_price, expect_revenue_percent, spread_percent, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "BBB", "2026-06-02 09:30:00", 0, "", 100, 100, 99, 101, 101, 1.0, 0.2, "{}", "2026-06-02 09:30:01"),
            ("2026-06-02", "scan_candidate", "AAA", "2026-06-02 09:30:00", 0, "", 100, 100, 99, 101, 103, 3.0, 0.2, "{}", "2026-06-02 09:30:05"),
            ("2026-06-02", "watchlist", "AAA", "2026-06-02 09:31:00", 0, "", 100, 103, 102, 104, 103, 3.0, 0.2, "{}", "2026-06-02 09:31:02"),
            ("2026-06-02", "watchlist", "BBB", "2026-06-02 09:31:00", 0, "", 100, 101, 100, 102, 101, 1.0, 0.2, "{}", "2026-06-02 09:31:01"),
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

    assert len(trades) >= 1
    assert trades[0].ticker == "AAA"
    assert trades[0].exit_reason == "take_profit"


def test_run_backtest_applies_previous_scan_jump_filter(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE market_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            phase TEXT NOT NULL,
            ticker TEXT NOT NULL,
            scan_cycle_at TEXT,
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
            scan_cycle_at TEXT,
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
        (session_date, phase, ticker, scan_cycle_at, selected, reason, price, current_price, best_bid, best_ask,
         expect_price, expect_revenue_percent, spread_percent, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "AAA", "2026-06-02 09:30:00", 0, "", 100, 100, 99, 101, 100, 0.1, 0.2, "{}", "2026-06-02 09:30:01"),
            ("2026-06-02", "scan_candidate", "BBB", "2026-06-02 09:30:00", 0, "", 100, 100, 99, 101, 100, 0.1, 0.2, "{}", "2026-06-02 09:30:02"),
            ("2026-06-02", "scan_candidate", "AAA", "2026-06-02 09:31:00", 0, "", 100, 102, 101, 103, 103, 1.0, 0.2, "{}", "2026-06-02 09:31:01"),
            ("2026-06-02", "scan_candidate", "BBB", "2026-06-02 09:31:00", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:02"),
            ("2026-06-02", "watchlist", "BBB", "2026-06-02 09:32:00", 0, "", 100, 101, 100, 102, 120, 1.0, 0.2, "{}", "2026-06-02 09:32:01"),
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
        max_intraday_jump_from_prev_scan_percent=1.0,
    )

    assert len(trades) == 1
    assert trades[0].ticker == "BBB"


def test_run_backtest_uses_affordable_target_selection_logic(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE market_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            phase TEXT NOT NULL,
            ticker TEXT NOT NULL,
            scan_cycle_at TEXT,
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
            scan_cycle_at TEXT,
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
        (session_date, phase, ticker, scan_cycle_at, selected, reason, price, current_price, best_bid, best_ask,
         expect_price, expect_revenue_percent, spread_percent, ask_depth_5_amount_krw, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-06-02", "scan_candidate", "HIGH", "2026-06-02 09:30:00", 0, "", 70000, 70000, 69900, 70100, 71000, 1.2, 0.2, 500000, "{}", "2026-06-02 09:30:01"),
            ("2026-06-02", "scan_candidate", "MID1", "2026-06-02 09:30:00", 0, "", 40000, 40000, 39900, 40100, 41000, 1.1, 0.2, 500000, "{}", "2026-06-02 09:30:02"),
            ("2026-06-02", "scan_candidate", "MID2", "2026-06-02 09:30:00", 0, "", 40000, 40000, 39900, 40100, 41000, 1.0, 0.2, 500000, "{}", "2026-06-02 09:30:03"),
            ("2026-06-02", "watchlist", "MID1", "2026-06-02 09:31:00", 0, "", 40000, 41000, 40900, 41100, 41000, 1.1, 0.2, 500000, "{}", "2026-06-02 09:31:01"),
            ("2026-06-02", "watchlist", "MID2", "2026-06-02 09:31:00", 0, "", 40000, 41000, 40900, 41100, 41000, 1.0, 0.2, 500000, "{}", "2026-06-02 09:31:02"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=2,
        stop_loss_percent=1.0,
        use_selected_signals=False,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=100_000,
        min_slot_count=1,
        max_slot_count=2,
        slot_budget_unit_krw=0,
        max_budget_per_stock_krw=0,
        target_budget_ratio_per_stock=0.5,
    )

    assert [trade.ticker for trade in trades] == ["MID1", "MID2"]


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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 101, 100, 102, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
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

    assert len(trades) == 1
    assert trades[0].ticker == "AAA"


def test_pick_candidates_with_fallback_allows_active_positions_when_refill_is_enabled():
    from Daily_bot.backtest.replay_market_traces import TraceRow, _pick_candidates_for_entry_with_fallback

    rows = [
        TraceRow(
            session_date="2026-06-02",
            ticker="AAA",
            created_at="2026-06-02 09:30:00",
            phase="scan_candidate",
            selected=0,
            price=100,
            prev_close_price=100,
            current_price=100,
            expect_price=101,
            expect_revenue_percent=0.4,
            spread_percent=0.2,
            ask_depth_5_amount_krw=0,
            prev_day_change_percent=0.0,
        ),
        TraceRow(
            session_date="2026-06-02",
            ticker="BBB",
            created_at="2026-06-02 09:30:00",
            phase="scan_candidate",
            selected=0,
            price=100,
            prev_close_price=100,
            current_price=100,
            expect_price=101,
            expect_revenue_percent=0.5,
            spread_percent=0.2,
            ask_depth_5_amount_krw=0,
            prev_day_change_percent=0.0,
        ),
    ]

    candidates, used_threshold = _pick_candidates_for_entry_with_fallback(
        rows=rows,
        min_expected_return_percent=0.6,
        fallback_min_expected_return_percents=[0.5],
        max_spread_percent=0.7,
        top_ratio=1.0,
        spread_expected_return_multiplier=0.0,
        min_prev_day_change_percent=0.0,
        max_prev_day_change_percent=15.0,
        active_tickers={"000660"},
        allowed_tickers=None,
        trend_allowed_tickers=None,
        previous_scan_prices={},
        max_intraday_jump_from_prev_scan_percent=0.0,
        allow_refill_empty_slots=True,
    )

    assert [candidate.ticker for candidate in candidates] == ["BBB"]
    assert used_threshold == 0.5


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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 11:29:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 11:31:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 120, 1.0, 0.2, "{}", "2026-06-02 15:00:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 101, 100, 102, 120, 1.0, 0.2, "{}", "2026-06-02 15:00:00"),
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


def test_run_backtest_exits_position_after_hold_timeout_when_target_never_fills(tmp_path):
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 100, 103, 2.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 99, 98, 99, 103, 2.0, 0.2, "{}", "2026-06-02 09:30:10"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 98, 97, 98, 103, 2.0, 0.2, "{}", "2026-06-02 09:30:21"),
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
        max_hold_seconds_before_exit=20,
    )

    assert len(trades) == 1
    assert trades[0].exit_reason == "time_stop_loss"
    assert trades[0].exit_time == "2026-06-02 09:30:21"
    assert trades[0].exit_price == 98


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
                ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
                ("2026-06-02", "watchlist", "AAA", 0, "", 100, 101, 100, 102, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
                ("2026-06-03", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-03 09:30:00"),
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


def test_resolve_replay_db_path_rebuilds_from_csv_logs_when_db_is_empty(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    db_path.write_bytes(b"")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    (logs_dir / "market_traces_20260610.csv").write_text(
        "\n".join(
            [
                "session_date,phase,ticker,selected,reason,price,current_price,best_bid,best_ask,expect_price,expect_revenue_percent,spread_percent,raw_json",
                    '2026-06-10,scan_candidate,AAA,0,main_scan,100,100,99,101,120,1.0,0.2,"{""bid_req_base_tm"": ""093000""}"',
                    '2026-06-10,watchlist,AAA,0,watch,100,101,100,102,120,1.0,0.2,"{""bid_req_base_tm"": ""093100""}"',
            ]
        ),
        encoding="utf-8-sig",
    )
    (logs_dir / "account_traces_20260610.csv").write_text(
        "\n".join(
            [
                "session_date,phase,cash,account_value,external_cash_flow,adjusted_account_value,adjusted_pnl,loss_percent,positions_json,open_orders_json",
                "2026-06-10,daily_loss_check,1000000,1000000,0,1000000,0,0.0,[],[]",
            ]
        ),
        encoding="utf-8-sig",
    )

    resolved_db_path = resolve_replay_db_path(db_path, logs_dir)

    assert resolved_db_path.exists()
    assert resolved_db_path != db_path

    trades = run_backtest(
        db_path=resolved_db_path,
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

    assert len(trades) == 1
    assert trades[0].ticker == "AAA"


def test_resolve_replay_db_path_backfills_prev_close_and_prev_day_change_from_overrides(tmp_path, monkeypatch):
    db_path = tmp_path / "bot.sqlite3"
    db_path.write_bytes(b"")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    (logs_dir / "market_traces_20260610.csv").write_text(
        "\n".join(
            [
                "session_date,phase,ticker,selected,reason,price,current_price,best_bid,best_ask,expect_price,expect_revenue_percent,spread_percent,raw_json",
                '2026-06-10,scan_candidate,AAA,0,main_scan,100,100,99,101,101,1.0,0.2,"{""bid_req_base_tm"": ""093000""}"',
            ]
        ),
        encoding="utf-8-sig",
    )

    monkeypatch.setattr(
        replay_db_builder,
        "_resolve_prev_close_price_overrides",
        lambda market_files: {("2026-06-10", "AAA"): 80},
    )

    resolved_db_path = resolve_replay_db_path(db_path, logs_dir)

    conn = sqlite3.connect(resolved_db_path)
    row = conn.execute(
        """
        SELECT prev_close_price, prev_day_change_percent
        FROM market_traces
        WHERE session_date = '2026-06-10' AND ticker = 'AAA'
        """
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 80
    assert row[1] == 25.0


def test_run_backtest_waits_for_full_batch_exit_before_rebuying(tmp_path):
    db_path = tmp_path / "replay.sqlite3"
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 120, 20.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 120, 119, 120, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 100, 99, 101, 120, 20.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "scan_candidate", "CCC", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 100, 99, 101, 120, 20.0, 0.2, "{}", "2026-06-02 09:32:00"),
            ("2026-06-02", "watchlist", "CCC", 0, "", 100, 120, 119, 120, 120, 1.0, 0.2, "{}", "2026-06-02 09:32:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=0,
        stop_loss_percent=0.0,
        use_selected_signals=False,
        take_profit_percent=0.25,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=0,
        slot_budget_unit_krw=500_000,
        max_budget_per_stock_krw=0,
        max_position_count=2,
        target_budget_ratio_per_stock=0.0,
        start_buy_time="09:30",
        stop_buy_time="11:30",
        force_sell_time="15:00",
    )

    assert [trade.ticker for trade in trades] == ["AAA", "BBB"]


def test_run_backtest_can_allow_refill_until_the_whole_batch_is_flat(tmp_path):
    db_path = tmp_path / "replay.sqlite3"
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "scan_candidate", "BBB", 0, "", 100, 100, 99, 101, 120, 20.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 120, 119, 120, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 100, 99, 101, 120, 20.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "scan_candidate", "CCC", 0, "", 100, 100, 99, 101, 120, 1.0, 0.2, "{}", "2026-06-02 09:31:00"),
            ("2026-06-02", "watchlist", "BBB", 0, "", 100, 100, 99, 101, 120, 20.0, 0.2, "{}", "2026-06-02 09:32:00"),
            ("2026-06-02", "watchlist", "CCC", 0, "", 100, 120, 119, 120, 120, 1.0, 0.2, "{}", "2026-06-02 09:32:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=0,
        stop_loss_percent=0.0,
        use_selected_signals=False,
        take_profit_percent=0.25,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=0,
        slot_budget_unit_krw=500_000,
        max_budget_per_stock_krw=0,
        max_position_count=2,
        target_budget_ratio_per_stock=0.0,
        start_buy_time="09:30",
        stop_buy_time="11:30",
        force_sell_time="15:00",
        allow_refill_empty_slots=True,
    )

    assert [trade.ticker for trade in trades] == ["AAA", "CCC", "BBB"]


def test_run_backtest_take_profit_uses_target_price_when_trace_overshoots(tmp_path):
    db_path = tmp_path / "replay.sqlite3"
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
            ("2026-06-02", "scan_candidate", "AAA", 0, "", 100, 100, 99, 101, 102, 2.0, 0.2, "{}", "2026-06-02 09:30:00"),
            ("2026-06-02", "watchlist", "AAA", 0, "", 100, 110, 109, 111, 102, 2.0, 0.2, "{}", "2026-06-02 09:31:00"),
        ],
    )
    conn.commit()
    conn.close()

    trades = run_backtest(
        db_path=db_path,
        min_expected_return_percent=0.25,
        max_spread_percent=0.7,
        top_n_per_day=1,
        stop_loss_percent=0.0,
        use_selected_signals=False,
        take_profit_percent=0.25,
        top_ratio=1.0,
        sell_tick_offset=1,
        default_starting_capital_krw=1_000_000,
        min_slot_count=1,
        max_slot_count=1,
        slot_budget_unit_krw=1_000_000,
        max_budget_per_stock_krw=1_000_000,
        start_buy_time="09:30",
        stop_buy_time="11:30",
        force_sell_time="15:00",
    )

    assert len(trades) == 1
    assert trades[0].exit_reason == "take_profit"
    assert trades[0].entry_price == 100
    assert trades[0].exit_price == 101
