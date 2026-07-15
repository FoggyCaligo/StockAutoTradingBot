from datetime import datetime
from pathlib import Path
import sqlite3

from Daily_bot.models import Candidate, Fill, HogaLevel, HogaSnapshot
from Daily_bot.storage.db import Recorder


def test_recorder_migrates_legacy_logs_into_default_log_dir(tmp_path):
    legacy_dir = tmp_path / "Daily_bot" / "logs"
    legacy_dir.mkdir(parents=True)
    legacy_csv = legacy_dir / "market_traces_20260605.csv"
    legacy_csv.write_text("col_a,col_b\n1,2\n", encoding="utf-8-sig")

    recorder = Recorder(tmp_path / "bot.sqlite3")

    migrated_csv = tmp_path / "logs" / "market_traces_20260605.csv"
    assert migrated_csv.exists()
    assert "1,2" in migrated_csv.read_text(encoding="utf-8-sig")
    assert not legacy_csv.exists()
    recorder.conn.close()


def test_recorder_merges_legacy_csv_rows_when_target_already_exists(tmp_path):
    target_dir = tmp_path / "logs"
    target_dir.mkdir(parents=True)
    target_csv = target_dir / "orders_20260605.csv"
    target_csv.write_text("ticker,price\nAAA,1000\n", encoding="utf-8-sig")

    legacy_dir = tmp_path / "Daily_bot" / "logs"
    legacy_dir.mkdir(parents=True)
    legacy_csv = legacy_dir / "orders_20260605.csv"
    legacy_csv.write_text("ticker,price\nBBB,2000\n", encoding="utf-8-sig")

    recorder = Recorder(tmp_path / "bot.sqlite3")

    merged_text = target_csv.read_text(encoding="utf-8-sig")
    assert "AAA,1000" in merged_text
    assert "BBB,2000" in merged_text
    assert merged_text.count("ticker,price") == 1
    assert not legacy_csv.exists()
    recorder.conn.close()


def test_recorder_persists_scan_cycle_at_for_scan_outputs(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")
    scan_cycle_at = datetime(2026, 6, 11, 9, 30, 0)
    kospi_change_percent = -1.23
    candidate = Candidate(
        ticker="005930",
        price=70_000,
        expect_price=70_300,
        expect_revenue_percent=0.3,
        spread_percent=0.1,
        ask_depth_5_amount_krw=1_500_000,
        market_cap=420_000_000_000,
        trading_value=8_500_000_000,
    )
    snapshot = HogaSnapshot(
        ticker="005930",
        current_price=70_000,
        bids=[HogaLevel(69_900, 10)],
        asks=[HogaLevel(70_000, 12)],
        captured_at=datetime(2026, 6, 11, 9, 30, 2),
    )

    recorder.save_snapshot(candidate, snapshot, scan_cycle_at=scan_cycle_at)
    recorder.save_signal(candidate, selected=False, scan_cycle_at=scan_cycle_at)
    recorder.save_market_trace(
        candidate,
        snapshot,
        phase="scan_candidate",
        selected=False,
        reason="main_scan",
        scan_cycle_at=scan_cycle_at,
        kospi_change_percent=kospi_change_percent,
    )

    snapshot_row = recorder.conn.execute(
        "SELECT scan_cycle_at FROM hoga_snapshots WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        ("005930",),
    ).fetchone()
    signal_row = recorder.conn.execute(
        "SELECT scan_cycle_at FROM signals WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        ("005930",),
    ).fetchone()
    trace_row = recorder.conn.execute(
        "SELECT scan_cycle_at, kospi_change_percent, ask_depth_5_amount_krw, market_cap, trading_value FROM market_traces WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        ("005930",),
    ).fetchone()

    assert snapshot_row["scan_cycle_at"] == scan_cycle_at.isoformat()
    assert signal_row["scan_cycle_at"] == scan_cycle_at.isoformat()
    assert trace_row["scan_cycle_at"] == scan_cycle_at.isoformat()
    assert trace_row["kospi_change_percent"] == kospi_change_percent
    assert trace_row["ask_depth_5_amount_krw"] == 1_500_000
    assert trace_row["market_cap"] == 420_000_000_000
    assert trace_row["trading_value"] == 8_500_000_000

    market_trace_csv = tmp_path / "logs" / f"market_traces_{datetime.now().strftime('%Y%m%d')}.csv"
    market_trace_csv_text = market_trace_csv.read_text(encoding="utf-8-sig")
    assert "scan_cycle_at" in market_trace_csv_text
    assert "kospi_change_percent" in market_trace_csv_text
    assert "ask_depth_5_amount_krw" in market_trace_csv_text
    assert "market_cap" in market_trace_csv_text
    assert "trading_value" in market_trace_csv_text
    recorder.conn.close()


def test_recorder_persists_kospi_change_percent_for_account_and_fill_audit(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")
    recorder.save_account_trace(
        phase="daily_loss_check",
        cash=1_000_000,
        account_value=1_100_000,
        positions=[],
        open_orders=[],
        external_cash_flow=0,
        adjusted_account_value=1_100_000,
        adjusted_pnl=100_000,
        loss_percent=-10.0,
        kospi_change_percent=-0.87,
    )
    recorder.save_fill(
        Fill(
            order_id="BUY-1",
            ticker="005930",
            quantity=2,
            price=70_000,
            filled_at=datetime(2026, 6, 11, 9, 31, 0),
        ),
        side="BUY",
        source="test",
    )

    account_row = recorder.conn.execute(
        "SELECT kospi_change_percent FROM account_traces ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert account_row["kospi_change_percent"] == -0.87

    account_trace_csv = tmp_path / "logs" / f"account_traces_{datetime.now().strftime('%Y%m%d')}.csv"
    assert "kospi_change_percent" in account_trace_csv.read_text(encoding="utf-8-sig")

    audit_csv = tmp_path / "logs" / "trade_fills_audit.csv"
    audit_text = audit_csv.read_text(encoding="utf-8-sig")
    assert "kospi_change_percent" in audit_text
    assert "-0.87" in audit_text
    recorder.conn.close()


def test_recorder_persists_daily_reference_prices(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")
    candidates = {
        "005930": Candidate(
            ticker="005930",
            name="Samsung Electronics",
            prev_close_price=70_000,
            market_cap=420_000_000_000,
            trading_value=8_500_000_000,
            trend_ok=True,
        ),
        "000660": Candidate(
            ticker="000660",
            name="SK hynix",
            prev_close_price=210_000,
            market_cap=150_000_000_000,
            trading_value=7_000_000_000,
            trend_ok=False,
        ),
    }

    recorder.save_daily_reference_prices(candidates, session_date="2026-06-24")

    price_map = recorder.get_daily_reference_prices("2026-06-24")
    assert price_map == {"005930": 70_000, "000660": 210_000}

    rows = recorder.conn.execute(
        """
        SELECT ticker, prev_close_price, trend_ok, source
        FROM daily_reference_prices
        WHERE session_date = ?
        ORDER BY ticker
        """,
        ("2026-06-24",),
    ).fetchall()
    assert [(row["ticker"], row["prev_close_price"], row["trend_ok"], row["source"]) for row in rows] == [
        ("000660", 210_000, 0, "universe_startup"),
        ("005930", 70_000, 1, "universe_startup"),
    ]

    reference_csv = tmp_path / "logs" / f"daily_reference_prices_{datetime.now().strftime('%Y%m%d')}.csv"
    reference_csv_text = reference_csv.read_text(encoding="utf-8-sig")
    assert "prev_close_price" in reference_csv_text
    assert "Samsung Electronics" in reference_csv_text
    recorder.conn.close()


def test_rebuild_session_fill_exports_keeps_other_sessions_in_trade_fill_audit(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")
    recorder.save_fill(
        Fill(
            order_id="SELL-OLD",
            ticker="000001",
            quantity=1,
            price=1000,
            filled_at=datetime(2026, 6, 10, 15, 0, 0),
        ),
        side="SELL",
        source="test",
    )
    recorder.save_fill(
        Fill(
            order_id="SELL-NEW",
            ticker="000002",
            quantity=2,
            price=2000,
            filled_at=datetime(2026, 6, 11, 15, 0, 0),
        ),
        side="SELL",
        source="test",
    )

    recorder.rebuild_session_fill_exports("2026-06-11")

    audit_csv = tmp_path / "logs" / "trade_fills_audit.csv"
    audit_text = audit_csv.read_text(encoding="utf-8-sig")
    assert "SELL-OLD" in audit_text
    assert "SELL-NEW" in audit_text
    recorder.conn.close()


def test_trade_fill_audit_excludes_inferred_fill_sources(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")
    recorder.save_fill(
        Fill(
            order_id="BUY-REAL",
            ticker="005930",
            quantity=1,
            price=70000,
            filled_at=datetime(2026, 6, 11, 9, 31, 0),
        ),
        side="BUY",
        source="wait_buy_filled",
    )
    recorder.save_fill(
        Fill(
            order_id="BUY-INFERRED",
            ticker="005930",
            quantity=1,
            price=70000,
            filled_at=datetime(2026, 6, 11, 9, 31, 30),
        ),
        side="BUY",
        source="position_recovery",
    )
    recorder.save_fill(
        Fill(
            order_id="SELL-INFERRED",
            ticker="005930",
            quantity=1,
            price=69900,
            filled_at=datetime(2026, 6, 11, 9, 32, 0),
        ),
        side="SELL",
        source="sell_reconciliation",
    )

    audit_csv = tmp_path / "logs" / "trade_fills_audit.csv"
    audit_text = audit_csv.read_text(encoding="utf-8-sig")
    assert "BUY-REAL" in audit_text
    assert "BUY-INFERRED" not in audit_text
    assert "SELL-INFERRED" not in audit_text

    recorder.rebuild_session_fill_exports("2026-06-11")

    rebuilt_audit_text = audit_csv.read_text(encoding="utf-8-sig")
    assert "BUY-REAL" in rebuilt_audit_text
    assert "BUY-INFERRED" not in rebuilt_audit_text
    assert "SELL-INFERRED" not in rebuilt_audit_text
    recorder.conn.close()


def test_daily_trade_fill_audit_resets_running_state_per_trade_date(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")
    recorder.save_fill(
        Fill(
            order_id="BUY-D1",
            ticker="005930",
            quantity=1,
            price=10_000,
            filled_at=datetime(2026, 6, 10, 9, 31, 0),
        ),
        side="BUY",
        source="poll",
    )
    recorder.save_fill(
        Fill(
            order_id="SELL-D1",
            ticker="005930",
            quantity=1,
            price=10_100,
            filled_at=datetime(2026, 6, 10, 9, 32, 0),
        ),
        side="SELL",
        source="poll",
    )
    recorder.save_fill(
        Fill(
            order_id="BUY-D2",
            ticker="005930",
            quantity=2,
            price=20_000,
            filled_at=datetime(2026, 6, 11, 9, 31, 0),
        ),
        side="BUY",
        source="poll",
    )

    daily_audit_csv = tmp_path / "logs" / "trade_fills_audit_daily.csv"
    daily_audit_text = daily_audit_csv.read_text(encoding="utf-8-sig")

    assert "BUY-D1" in daily_audit_text
    assert "BUY-D2" in daily_audit_text
    assert "2026-06-11,2026-06-11T09:31:00,BUY-D2,005930,BUY,2,20000,40000,6.0,0.0,6.0,poll,,,,,,,2,40000,20000.0,0,0" in daily_audit_text
    recorder.conn.close()


def test_write_daily_revenue_summary_upserts_single_session_row(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")
    recorder.save_fill(
        Fill(
            order_id="BUY-1",
            ticker="005930",
            quantity=2,
            price=10_000,
            filled_at=datetime(2026, 6, 11, 9, 31, 0),
            raw={"rows": [{"tdy_trde_cmsn": "10", "tdy_trde_tax": "0"}]},
        ),
        side="BUY",
        source="poll",
    )
    recorder.save_fill(
        Fill(
            order_id="SELL-1",
            ticker="005930",
            quantity=2,
            price=10_100,
            filled_at=datetime(2026, 6, 11, 9, 32, 0),
            raw={"rows": [{"tdy_trde_cmsn": "11", "tdy_trde_tax": "36"}]},
        ),
        side="SELL",
        source="poll",
    )

    recorder.write_daily_revenue_summary("2026-06-11", starting_capital_krw=1_000_000)
    recorder.write_daily_revenue_summary("2026-06-11", starting_capital_krw=1_000_000)

    daily_rev_csv = tmp_path / "logs" / "daily_rev.csv"
    daily_rev_text = daily_rev_csv.read_text(encoding="utf-8-sig")
    assert daily_rev_text.count("2026-06-11") == 1
    assert "005930" in daily_rev_text
    assert "0.7150" in daily_rev_text
    assert "0.0143" in daily_rev_text
    recorder.conn.close()


def test_recorder_disables_db_sink_when_write_fails(tmp_path):
    recorder = Recorder(tmp_path / "bot.sqlite3")

    class _BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("database or disk is full")

        def commit(self):
            raise sqlite3.OperationalError("database or disk is full")

        def close(self):
            return None

    recorder.conn = _BrokenConnection()

    recorder.save_order(
        type(
            "Order",
            (),
            {
                "order_id": "ORDER-1",
                "ticker": "005930",
                "side": "BUY",
                "quantity": 1,
                "price": 70_000,
                "status": "ok",
                "raw": {},
            },
        )()
    )

    assert recorder._db_enabled is False
    assert recorder.conn is None


def test_recorder_disables_csv_sink_when_csv_write_fails(tmp_path, monkeypatch):
    recorder = Recorder(tmp_path / "bot.sqlite3")

    def _raise_disk_full(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "_append_csv_row", _raise_disk_full)

    recorder.save_order(
        type(
            "Order",
            (),
            {
                "order_id": "ORDER-1",
                "ticker": "005930",
                "side": "BUY",
                "quantity": 1,
                "price": 70_000,
                "status": "ok",
                "raw": {},
            },
        )()
    )

    assert recorder._csv_enabled is False
    recorder.conn.close()
