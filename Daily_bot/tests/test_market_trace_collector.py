from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Daily_bot import market_trace_collector as collector
from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot


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
