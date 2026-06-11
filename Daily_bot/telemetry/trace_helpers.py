from __future__ import annotations

from datetime import datetime
from typing import Any

from Daily_bot.models import Candidate
from Daily_bot.storage.db import Recorder
from Daily_bot.strategy.signal import calc_expected_return
from Daily_bot.utils import RateLimiter


def ticker_key(ticker: str) -> str:
    return str(ticker or "").strip().upper().removeprefix("A")


def record_scan_candidate(recorder: Recorder, candidate: Candidate, snapshot: Any, reason: str = "scan_candidate") -> None:
    recorder.save_market_trace(
        candidate,
        snapshot,
        phase="scan_candidate",
        selected=False,
        reason=reason,
        scan_cycle_at=datetime.now(),
    )


def trace_candidate_watchlist(
    client: Any,
    recorder: Recorder,
    candidates: dict[str, Candidate],
    quote_rate_limit_per_second: int,
    sell_tick_offset: int,
    selected_keys: set[str] | None = None,
    kospi_change_percent: float | None = None,
) -> dict[str, Candidate]:
    selected_keys = selected_keys or set()
    limiter = RateLimiter(quote_rate_limit_per_second)
    updated: dict[str, Candidate] = {}
    scan_cycle_at = datetime.now()
    for key, candidate in candidates.items():
        try:
            limiter.wait()
            snapshot = client.get_20hoga(candidate.ticker)
            traced = calc_expected_return(candidate, snapshot, sell_tick_offset)
            recorder.save_market_trace(
                traced,
                snapshot,
                phase="watchlist",
                selected=key in selected_keys,
                reason="filtered_candidate_recheck",
                scan_cycle_at=scan_cycle_at,
                kospi_change_percent=kospi_change_percent,
            )
            updated[key] = traced
        except Exception as exc:
            print(f"Failed to trace candidate {candidate.ticker}: {exc}")
            updated[key] = candidate
    return updated


def record_account_snapshot(
    client: Any,
    recorder: Recorder,
    phase: str,
    positions: list | None = None,
    open_orders: list[dict] | None = None,
    account_value: int | None = None,
    kospi_change_percent: float | None = None,
) -> None:
    try:
        cash = client.get_orderable_cash()
    except Exception as exc:
        print(f"Failed to record cash for {phase}: {exc}")
        cash = 0
    if positions is None:
        try:
            positions = client.get_positions()
        except Exception as exc:
            print(f"Failed to record positions for {phase}: {exc}")
            positions = []
    if open_orders is None:
        try:
            open_orders = client.get_open_orders()
        except Exception as exc:
            print(f"Failed to record open orders for {phase}: {exc}")
            open_orders = []
    recorder.save_account_trace(
        phase,
        cash,
        account_value or 0,
        positions,
        open_orders,
        kospi_change_percent=kospi_change_percent,
    )
