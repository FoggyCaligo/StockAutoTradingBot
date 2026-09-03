from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from Daily_bot.models import Candidate
from Daily_bot.storage.db import Recorder
from Daily_bot.strategy.signal import calc_expected_return
from Daily_bot.utils import RateLimiter, load_yaml


def ticker_key(ticker: str) -> str:
    return str(ticker or "").strip().upper().removeprefix("A")


def _default_strategy_cfg() -> dict:
    try:
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
        cfg = load_yaml(cfg_path)
        strategy_cfg = cfg.get("strategy", {}) if isinstance(cfg, dict) else {}
        return strategy_cfg if isinstance(strategy_cfg, dict) else {}
    except Exception as exc:
        print(f"Failed to load strategy config for market trace: {exc}")
        return {}


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
    strategy_cfg: dict | None = None,
) -> dict[str, Candidate]:
    selected_keys = selected_keys or set()
    effective_strategy_cfg = strategy_cfg if isinstance(strategy_cfg, dict) else _default_strategy_cfg()
    limiter = RateLimiter(quote_rate_limit_per_second)
    updated: dict[str, Candidate] = {}
    scan_cycle_at = datetime.now()
    for key, candidate in candidates.items():
        try:
            limiter.wait()
            snapshot = client.get_20hoga(candidate.ticker)
            traced = calc_expected_return(
                candidate,
                snapshot,
                sell_tick_offset=sell_tick_offset,
                strategy_cfg=effective_strategy_cfg,
            )
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


def trace_active_positions(
    client: Any,
    recorder: Recorder,
    positions: list,
    quote_rate_limit_per_second: int,
    kospi_change_percent: float | None = None,
    strategy_cfg: dict | None = None,
) -> None:
    if not positions:
        return
    effective_strategy_cfg = strategy_cfg if isinstance(strategy_cfg, dict) else _default_strategy_cfg()
    sell_tick_offset = int(effective_strategy_cfg.get("sell_tick_offset", 1) or 1)
    limiter = RateLimiter(quote_rate_limit_per_second)
    scan_cycle_at = datetime.now()
    for position in positions:
        quantity = int(getattr(position, "quantity", 0) or 0)
        ticker = getattr(position, "ticker", "")
        if quantity <= 0 or not ticker:
            continue
        try:
            limiter.wait()
            snapshot = client.get_20hoga(ticker)
            candidate = Candidate(
                ticker=ticker,
                price=int(getattr(position, "avg_price", 0) or 0),
            )
            traced = calc_expected_return(
                candidate,
                snapshot,
                sell_tick_offset=sell_tick_offset,
                strategy_cfg=effective_strategy_cfg,
            )
            recorder.save_market_trace(
                traced,
                snapshot,
                phase="active_position",
                selected=True,
                reason=f"held_position_monitor qty={quantity}",
                scan_cycle_at=scan_cycle_at,
                kospi_change_percent=kospi_change_percent,
            )
        except Exception as exc:
            print(f"Failed to trace active position {ticker}: {exc}")


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
