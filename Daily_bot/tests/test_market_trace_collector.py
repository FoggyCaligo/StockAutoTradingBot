from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Daily_bot import market_trace_collector as collector
from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot
from Daily_bot.storage.db import Recorder


def _settings():
    return {"api": {"quote_rate_limit_per_second": 100}, "strategy": {"sell_tick_offset": 1}}


def test_collect_cycle_writes_only_market_trace(monkeypatch):
    monkeypatch.setattr(collector, "RateLimiter", lambda _rate: SimpleNamespace(wait=lambda: None))
    monkeypatch.setattr(collector, "calc_expected_return", lambda candidate, _snapshot, *_args: candidate)
    snapshot = HogaSnapshot(
        ticker="005930", current_price=70000,
        bids=[HogaLevel(69900, 10)], asks=[HogaLevel(70000, 10)],
        captured_at=datetime.now(),
    )
    client = Mock()
    client.get_20hoga.return_value = snapshot
    recorder = Mock()
    recorder._db_enabled = True
    recorder._csv_enabled = True
    n, errors = collector.collect_cycle(
        client, recorder, {"005930": Candidate(ticker="005930", price=70000)}, _settings()
    )
    assert (n, errors) == (1, 0)
    recorder.save_market_trace.assert_called_once()
    assert recorder.save_market_trace.call_args.kwargs["phase"] == "scan_candidate"
    assert not client.mock_calls or all(call[0] == "get_20hoga" for call in client.mock_calls)


def test_collect_cycle_fails_fast_when_recorder_disabled(monkeypatch):
    monkeypatch.setattr(collector, "RateLimiter", lambda _rate: SimpleNamespace(wait=lambda: None))
    monkeypatch.setattr(collector, "calc_expected_return", lambda candidate, _snapshot, *_args: candidate)
    client = Mock()
    client.get_20hoga.return_value = HogaSnapshot(
        ticker="005930", current_price=70000, bids=[], asks=[], captured_at=datetime.now()
    )
    recorder = Mock()
    recorder._db_enabled = False
    recorder._csv_enabled = True
    with pytest.raises(RuntimeError, match="recording stopped"):
        collector.collect_cycle(client, recorder, {"005930": Candidate(ticker="005930")}, _settings())


def test_collect_cycle_persists_real_db_and_csv(monkeypatch, tmp_path):
    import csv

    monkeypatch.setattr(collector, "RateLimiter", lambda _: SimpleNamespace(wait=lambda: None))
    client = Mock()
    client.get_20hoga.return_value = HogaSnapshot(
        ticker="005930", current_price=70000,
        bids=[HogaLevel(69900, 10)], asks=[HogaLevel(70000, 20)],
        raw={"buy_fpr_bid": "69900", "sel_fpr_bid": "70000"},
    )
    recorder = Recorder(tmp_path / "bot.sqlite3")
    try:
        assert collector.collect_cycle(client, recorder, {"005930": Candidate(ticker="005930")}, _settings()) == (1, 0)
        row = recorder.conn.execute("SELECT * FROM market_traces").fetchone()
        assert (row["ticker"], row["phase"], row["reason"]) == ("005930", "scan_candidate", "quote_only_collection")
        trace_file, = recorder.log_dir.glob("market_traces_*.csv")
        with trace_file.open(encoding="utf-8-sig", newline="") as stream:
            records = list(csv.DictReader(stream))
        assert len(records) == 1
        assert records[0]["ticker"] == "005930"
        assert records[0]["raw_json"] == row["raw_json"]
    finally:
        recorder.conn.close()


@pytest.mark.parametrize("initial_result", [RuntimeError("temporary universe outage"), {}])
def test_run_retries_universe_and_uses_readonly_client(monkeypatch, tmp_path, initial_result):
    cfg = {
        **_settings(), "risk": {"dry_run": False},
        "market": {"start_buy_time": "09:30", "end_time": "15:25"},
        "strategy": {"sell_tick_offset": 1, "scan_interval_seconds": 1},
        "trend_filter": {"enabled": False},
    }
    client = SimpleNamespace(is_real_trade=False)
    build = Mock(return_value=client)
    universe = Mock(side_effect=[initial_result, {"005930": Candidate(ticker="005930")}])
    save = Mock(return_value=(1, 0))
    monkeypatch.setattr(collector, "load_yaml", lambda _: cfg)
    monkeypatch.setattr(collector.bot_main, "build_client", build)
    monkeypatch.setattr(collector.bot_main, "BOT_DB_PATH", tmp_path / "bot.sqlite3")
    monkeypatch.setattr(collector.bot_main, "BOT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(collector.bot_main, "build_universe_config", lambda _: None)
    monkeypatch.setattr(collector.bot_main, "_authenticate_client_safely", lambda _: True)
    ends = iter([False, False, True])
    monkeypatch.setattr(collector.bot_main, "is_after_now", lambda value: True if value == "09:30" else next(ends))
    monkeypatch.setattr(collector, "get_candidates", universe)
    monkeypatch.setattr(collector, "collect_cycle", save)
    monkeypatch.setattr(collector.time, "sleep", lambda _: None)

    collector.run("ignored.yaml")

    assert universe.call_count == 2
    save.assert_called_once()
    build.assert_called_once_with(False, enable_trading=False)


def test_run_still_stops_after_actual_storage_failure(monkeypatch, tmp_path):
    cfg = {
        **_settings(), "market": {"start_buy_time": "09:30", "end_time": "15:25"},
        "strategy": {"sell_tick_offset": 1, "scan_interval_seconds": 1},
        "trend_filter": {"enabled": False},
    }
    recorder = Recorder(tmp_path / "bot.sqlite3")
    monkeypatch.setattr(collector, "load_yaml", lambda _: cfg)
    monkeypatch.setattr(collector, "Recorder", lambda *_args, **_kwargs: recorder)
    monkeypatch.setattr(collector.bot_main, "build_client", lambda *_args, **_kwargs: SimpleNamespace(is_real_trade=False))
    monkeypatch.setattr(collector.bot_main, "_authenticate_client_safely", lambda _: True)
    monkeypatch.setattr(collector.bot_main, "is_after_now", lambda value: value == "09:30")
    monkeypatch.setattr(collector.bot_main, "build_universe_config", lambda _: None)
    monkeypatch.setattr(collector, "get_candidates", lambda *_: {"005930": Candidate(ticker="005930")})

    def fail_write(*_args):
        recorder._csv_enabled = False
        raise RuntimeError("CSV permission denied")

    monkeypatch.setattr(collector, "collect_cycle", fail_write)
    with pytest.raises(RuntimeError, match="DB/CSV write failure"):
        collector.run("ignored.yaml")


def test_zero_saved_cycle_refreshes_universe(monkeypatch, tmp_path):
    cfg = {
        **_settings(), "market": {"start_buy_time": "09:30", "end_time": "15:25"},
        "strategy": {"sell_tick_offset": 1, "scan_interval_seconds": 1},
        "trend_filter": {"enabled": False},
    }
    universe = Mock(return_value={"005930": Candidate(ticker="005930")})
    monkeypatch.setattr(collector, "load_yaml", lambda _: cfg)
    monkeypatch.setattr(collector.bot_main, "build_client", lambda *_args, **_kwargs: SimpleNamespace(is_real_trade=False))
    monkeypatch.setattr(collector.bot_main, "BOT_DB_PATH", tmp_path / "bot.sqlite3")
    monkeypatch.setattr(collector.bot_main, "BOT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(collector.bot_main, "_authenticate_client_safely", lambda _: True)
    monkeypatch.setattr(collector.bot_main, "build_universe_config", lambda _: None)
    ends = iter([False, False, True])
    monkeypatch.setattr(collector.bot_main, "is_after_now", lambda value: True if value == "09:30" else next(ends))
    monkeypatch.setattr(collector, "get_candidates", universe)
    monkeypatch.setattr(collector, "collect_cycle", Mock(side_effect=[(0, 1), (1, 0)]))
    monkeypatch.setattr(collector.time, "sleep", lambda _: None)
    collector.run("ignored.yaml")
    assert universe.call_count == 2
