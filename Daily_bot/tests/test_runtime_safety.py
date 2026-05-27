from dataclasses import dataclass

import Daily_bot.main as main
from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot


@dataclass
class _RecorderStub:
    snapshots: list[str]
    signals: list[str]

    def save_snapshot(self, candidate, snapshot) -> None:
        self.snapshots.append(candidate.ticker)

    def save_signal(self, candidate, selected: bool = False) -> None:
        self.signals.append(candidate.ticker)


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
    recorder = _RecorderStub(snapshots=[], signals=[])
    cfg = {
        "universe": {
            "min_price": 5_000,
            "max_price": 100_000,
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
