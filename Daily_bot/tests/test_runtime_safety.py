from dataclasses import dataclass

import Daily_bot.main as main
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
