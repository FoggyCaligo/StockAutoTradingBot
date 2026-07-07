from dataclasses import dataclass

import Daily_bot.main as main
import pytest
from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot
from Daily_bot.risk.stop_loss import _get_ticker
from Daily_bot.telemetry.trace_helpers import trace_active_positions


@dataclass
class _RecorderStub:
    snapshots: list[str]
    signals: list[str]
    traces: list[tuple[str, str, str]] | None = None

    def save_snapshot(self, candidate, snapshot, scan_cycle_at=None) -> None:
        self.snapshots.append(candidate.ticker)

    def save_signal(self, candidate, selected: bool = False, scan_cycle_at=None) -> None:
        self.signals.append(candidate.ticker)

    def save_market_trace(
        self,
        candidate,
        snapshot,
        phase: str,
        selected: bool = False,
        reason: str = "",
        scan_cycle_at=None,
        kospi_change_percent=None,
    ) -> None:
        if self.traces is None:
            self.traces = []
        self.traces.append((candidate.ticker, phase, reason))


class _ClientStub:
    def get_20hoga(self, ticker: str) -> HogaSnapshot:
        if ticker == "000660":
            raise RuntimeError("hoga parse failed")
        return HogaSnapshot(
            ticker=ticker,
            current_price=10_000,
            bids=[HogaLevel(9_950, 100)],
            asks=[HogaLevel(10_050, 100)],
        )


def test_scan_and_rank_skips_ticker_when_hoga_fetch_fails(monkeypatch):
    candidates = {
        "005930": Candidate(ticker="005930", price=10_000),
        "000660": Candidate(ticker="000660", price=10_000),
    }
    recorder = _RecorderStub(snapshots=[], signals=[], traces=[])
    cfg = {
        "universe": {
            "min_market_cap_krw": 1,
            "min_trading_value_krw": 1,
        },
        "trend_filter": {"enabled": False},
        "api": {"quote_rate_limit_per_second": 1000},
        "strategy": {"sell_tick_offset": 1},
    }

    monkeypatch.setattr(main, "get_candidates", lambda *_args, **_kwargs: candidates)

    ranked = main.scan_and_rank(_ClientStub(), recorder, cfg)

    assert [candidate.ticker for candidate in ranked] == ["005930"]
    assert recorder.snapshots == ["005930"]
    assert recorder.signals == ["005930"]
    assert recorder.traces == [("005930", "scan_candidate", "main_scan")]


def test_scan_and_rank_uses_recorded_prev_close_prices(monkeypatch):
    candidates = {
        "005930": Candidate(ticker="005930", price=10_000, prev_close_price=0),
    }
    recorder = _RecorderStub(snapshots=[], signals=[], traces=[])
    cfg = {
        "universe": {
            "min_market_cap_krw": 1,
            "min_trading_value_krw": 1,
        },
        "trend_filter": {"enabled": False},
        "api": {"quote_rate_limit_per_second": 1000},
        "strategy": {"sell_tick_offset": 1},
    }

    monkeypatch.setattr(main, "get_candidates", lambda *_args, **_kwargs: candidates)

    ranked = main.scan_and_rank(
        _ClientStub(),
        recorder,
        cfg,
        prev_close_prices={"005930": 9_800},
    )

    assert ranked[0].prev_close_price == 9_800
    assert ranked[0].prev_day_change_percent > 0


def test_trace_active_positions_records_remaining_positions_when_one_hoga_fetch_fails():
    recorder = _RecorderStub(snapshots=[], signals=[], traces=[])
    positions = [
        type("Position", (), {"ticker": "005930", "quantity": 3, "avg_price": 10_000})(),
        type("Position", (), {"ticker": "000660", "quantity": 2, "avg_price": 20_000})(),
    ]

    trace_active_positions(
        client=_ClientStub(),
        recorder=recorder,
        positions=positions,
        quote_rate_limit_per_second=1000,
        kospi_change_percent=-0.5,
    )

    assert recorder.traces == [("005930", "active_position", "held_position_monitor qty=3")]


def test_filter_candidates_by_prev_scan_jump_excludes_candidates_at_or_above_threshold():
    candidates = [
        Candidate(ticker="A005930", price=10_100),
        Candidate(ticker="A000660", price=10_090),
        Candidate(ticker="A035420", price=10_000),
    ]

    result = main.filter_candidates_by_prev_scan_jump(
        candidates,
        previous_scan_prices={"005930": 10_000, "000660": 10_000},
        max_intraday_jump_from_prev_scan_percent=1.0,
    )

    assert [candidate.ticker for candidate in result] == ["A000660", "A035420"]


def test_filter_candidates_by_prev_scan_jump_can_be_disabled():
    candidates = [Candidate(ticker="A005930", price=10_500)]

    result = main.filter_candidates_by_prev_scan_jump(
        candidates,
        previous_scan_prices={"005930": 10_000},
        max_intraday_jump_from_prev_scan_percent=0.0,
    )

    assert [candidate.ticker for candidate in result] == ["A005930"]


def test_stop_loss_get_ticker_normalizes_a_prefixed_codes():
    assert _get_ticker({"stk_cd": "A005930"}) == "005930"


def test_attempt_force_sell_safely_recovers_instead_of_raising(monkeypatch):
    recorder = _RecorderStub(snapshots=[], signals=[], traces=[])

    class _RiskClient:
        def get_positions(self):
            return [type("Position", (), {"ticker": "005930", "quantity": 1, "avg_price": 10_000})()]

        def get_open_orders(self):
            return [{"order_id": "SELL-1", "ticker": "005930", "side": "SELL", "ord_qty": "1"}]

    monkeypatch.setattr(main, "force_sell", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cancel timeout")))
    monkeypatch.setattr(main, "poll_and_record_new_fills", lambda *_args, **_kwargs: None)

    assert main._attempt_force_sell_safely(_RiskClient(), recorder) is False


def test_attempt_stop_loss_safely_recovers_instead_of_raising(monkeypatch):
    recorder = _RecorderStub(snapshots=[], signals=[], traces=[])

    class _RiskClient:
        def get_positions(self):
            return [type("Position", (), {"ticker": "005930", "quantity": 1, "avg_price": 10_000})()]

        def get_open_orders(self):
            return [{"order_id": "SELL-1", "ticker": "005930", "side": "SELL", "ord_qty": "1"}]

    monkeypatch.setattr(
        main,
        "monitor_stop_loss",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stop-loss cancel timeout")),
    )
    monkeypatch.setattr(main, "poll_and_record_new_fills", lambda *_args, **_kwargs: None)

    triggered, errored = main._attempt_stop_loss_safely(_RiskClient(), recorder, positions=[], open_orders=[], cfg={})

    assert triggered is False
    assert errored is True


def test_fetch_account_state_safely_recovers_instead_of_raising():
    class _RiskClient:
        def get_positions(self):
            raise RuntimeError("temporary disconnect")

        def get_open_orders(self):
            return []

    positions, open_orders = main._fetch_account_state_safely(_RiskClient(), "Main loop account state")

    assert positions is None
    assert open_orders is None


def test_authenticate_client_safely_recovers_instead_of_raising():
    class _RiskClient:
        def auth(self):
            raise RuntimeError("auth timeout")

    assert main._authenticate_client_safely(_RiskClient()) is False


def test_run_retries_main_loop_after_transient_account_state_failure(monkeypatch):
    class _StopLoop(Exception):
        pass

    class _LoopClient:
        def auth(self):
            return "token"

    class _RunRecorder:
        def __init__(self, *_args, **_kwargs):
            pass

    cfg = {
        "risk": {"dry_run": True},
        "market": {
            "prewarm_start_time": "08:50",
            "startup_clear_time": "09:10",
            "start_buy_time": "09:30",
            "stop_buy_time": "15:00",
            "force_sell_time": "15:10",
            "reconcile_time": "15:15",
            "end_time": "15:20",
        },
        "strategy": {
            "scan_interval_seconds": 60,
            "sell_tick_offset": 1,
            "allow_refill_empty_slots": True,
        },
        "api": {"quote_rate_limit_per_second": 1000},
    }
    fetch_attempts = {"count": 0}

    def _fake_is_after_now(value: str) -> bool:
        return value in {"09:10", "09:30"}

    def _fake_fetch_account_state_safely(_client, _label="Account state"):
        fetch_attempts["count"] += 1
        if fetch_attempts["count"] == 1:
            return None, None
        raise _StopLoop()

    monkeypatch.setattr(main, "load_yaml", lambda *_args, **_kwargs: cfg)
    monkeypatch.setattr(main, "build_client", lambda *_args, **_kwargs: _LoopClient())
    monkeypatch.setattr(main, "Recorder", _RunRecorder)
    monkeypatch.setattr(main, "estimate_account_value", lambda *_args, **_kwargs: 1_000_000)
    monkeypatch.setattr(main, "resolve_kospi_change_percent", lambda: 0.0)
    monkeypatch.setattr(main, "is_between_now", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(main, "is_after_now", _fake_is_after_now)
    monkeypatch.setattr(main, "_attempt_startup_carryover_liquidation_safely", lambda *_args, **_kwargs: (True, False))
    monkeypatch.setattr(main, "record_session_prev_close_prices", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "resolve_session_capital_basis", lambda *_args, **_kwargs: 1_000_000)
    monkeypatch.setattr(main, "resolve_total_slot_count", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(main, "resolve_target_budget_per_stock", lambda *_args, **_kwargs: 1_000_000)
    monkeypatch.setattr(main, "resolve_position_limit", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(main, "_fetch_account_state_safely", _fake_fetch_account_state_safely)
    monkeypatch.setattr(main.time, "sleep", lambda *_args, **_kwargs: None)

    with pytest.raises(_StopLoop):
        main.run("ignored.yaml", dry_run_override=True)

    assert fetch_attempts["count"] == 2


def test_filter_candidates_for_entry_uses_fallback_threshold_when_batch_is_flat():
    calculated = [
        Candidate(
            ticker="005930",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.5,
            spread_percent=0.1,
            trend_ok=True,
        )
    ]
    cfg = {
        "strategy": {
            "top_ratio": 1.0,
            "sell_tick_offset": 1,
            "min_expected_return_percent": 0.6,
            "min_expected_return_fallback_percents": [0.5, 0.4],
            "max_spread_percent": 0.7,
            "min_prev_day_change_percent": 0.0,
            "max_prev_day_change_percent": 15.0,
            "spread_expected_return_multiplier": 0.0,
            "max_intraday_jump_from_prev_scan_percent": 1.0,
        }
    }

    filtered, used_threshold = main.filter_candidates_for_entry(
        calculated,
        cfg,
        previous_scan_prices={},
        active_tickers=set(),
    )

    assert [candidate.ticker for candidate in filtered] == ["005930"]
    assert used_threshold == 0.5


def test_filter_candidates_for_entry_does_not_use_fallback_when_positions_are_active():
    calculated = [
        Candidate(
            ticker="005930",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.5,
            spread_percent=0.1,
            trend_ok=True,
        )
    ]
    cfg = {
        "strategy": {
            "top_ratio": 1.0,
            "sell_tick_offset": 1,
            "min_expected_return_percent": 0.6,
            "min_expected_return_fallback_percents": [0.5, 0.4],
            "max_spread_percent": 0.7,
            "min_prev_day_change_percent": 0.0,
            "max_prev_day_change_percent": 15.0,
            "spread_expected_return_multiplier": 0.0,
            "max_intraday_jump_from_prev_scan_percent": 1.0,
        }
    }

    filtered, used_threshold = main.filter_candidates_for_entry(
        calculated,
        cfg,
        previous_scan_prices={},
        active_tickers={"000660"},
    )

    assert filtered == []
    assert used_threshold == 0.6


def test_filter_candidates_for_entry_tries_multiple_fallback_thresholds_in_order():
    calculated = [
        Candidate(
            ticker="005930",
            price=10_000,
            expect_price=10_106,
            expect_revenue_percent=0.56,
            spread_percent=0.1,
            trend_ok=True,
        )
    ]
    cfg = {
        "strategy": {
            "top_ratio": 1.0,
            "sell_tick_offset": 1,
            "min_expected_return_percent": 0.7,
            "min_expected_return_fallback_percents": [0.6, 0.5],
            "max_spread_percent": 0.7,
            "min_prev_day_change_percent": 0.0,
            "max_prev_day_change_percent": 15.0,
            "spread_expected_return_multiplier": 0.0,
            "max_intraday_jump_from_prev_scan_percent": 1.0,
        }
    }

    filtered, used_threshold = main.filter_candidates_for_entry(
        calculated,
        cfg,
        previous_scan_prices={},
        active_tickers=set(),
    )

    assert [candidate.ticker for candidate in filtered] == ["005930"]
    assert used_threshold == 0.5
