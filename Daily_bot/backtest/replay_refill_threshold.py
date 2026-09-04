from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest import replay_dynamic_expect_debounce_reserve_stopped_slots as reserve


def main() -> None:
    """Replay the live-aligned capital-based batch/refill policy.

    Policy:
    - The normal capital-based session position limit is used; it is not hard-coded to 3.
    - Before the current batch has ever reached its full effective limit, never-used
      slots use the normal entry threshold.
    - Slots returned by ``take_profit`` use ``--refill-min-expected-return``.
    - Dynamic-stop slots remain reserved for the rest of that session via the reserve runner.
    - Once the batch first reaches its full effective limit, no further buys are
      allowed until every position in that batch has exited. A new batch may then start.

    This module is backtest-only and does not alter live trading behavior.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--refill-min-expected-return", type=float, default=0.90)
    custom, remaining = parser.parse_known_args()

    base = reserve.replay.base
    state = {
        "session_limit": 0,
        "closed_slots": 0,
        "active_count": 0,
        "batch_peak": 0,
        "batch_full_locked": False,
        "take_profit_slots": 0,
    }

    original_trade = base.BacktestTrade
    original_select = base.select_affordable_targets
    original_resolve = base.resolve_session_capital_plan

    def effective_limit() -> int:
        limit = max(0, int(state["session_limit"]))
        return max(0, limit - max(0, int(state["closed_slots"])))

    def reset_batch_if_flat() -> None:
        if state["batch_full_locked"] and state["active_count"] <= 0:
            state["batch_full_locked"] = False
            state["batch_peak"] = 0
            state["take_profit_slots"] = 0

    def resolve_with_reset(*args, **kwargs):
        plan = original_resolve(*args, **kwargs)
        state.update(
            session_limit=max(0, int(plan.position_limit or 0)),
            closed_slots=0,
            active_count=0,
            batch_peak=0,
            batch_full_locked=False,
            take_profit_slots=0,
        )
        return plan

    def tracked_trade(*args, **kwargs):
        trade = original_trade(*args, **kwargs)
        if state["active_count"] > 0:
            state["active_count"] -= 1
        if trade.exit_reason == "take_profit":
            state["take_profit_slots"] += 1
        elif trade.exit_reason in {"dynamic_expect_stop", "stop_loss", "time_stop_loss"}:
            # The live slot policy closes a stopped slot for the remainder of the day.
            state["closed_slots"] += 1
        reset_batch_if_flat()
        return trade

    def call_original(pool, count, args, kwargs, available_cash=None):
        if count <= 0 or not pool:
            return []
        patched_kwargs = dict(kwargs)
        patched_args = list(args)
        if patched_args:
            patched_args[0] = pool
        else:
            patched_kwargs["candidates"] = pool
        if "max_buy_count" in patched_kwargs:
            patched_kwargs["max_buy_count"] = count
        elif len(patched_args) > 1:
            patched_args[1] = count
        else:
            patched_kwargs["max_buy_count"] = count
        if available_cash is not None:
            if "available_cash_krw" in patched_kwargs:
                patched_kwargs["available_cash_krw"] = available_cash
            elif len(patched_args) > 2:
                patched_args[2] = available_cash
        return original_select(*patched_args, **patched_kwargs)

    def select_with_batch_policy(*args, **kwargs):
        candidates = list(args[0] if args else kwargs.get("candidates", []))
        requested_count = int(kwargs.get("max_buy_count", args[1] if len(args) > 1 else 0) or 0)
        if requested_count <= 0 or not candidates:
            return original_select(*args, **kwargs)

        reset_batch_if_flat()
        if state["batch_full_locked"] and state["active_count"] > 0:
            return []

        limit = effective_limit()
        if limit <= 0:
            return []

        available_capacity = max(0, limit - int(state["active_count"]))
        max_buy_count = min(requested_count, available_capacity)
        if max_buy_count <= 0:
            return []

        effective_peak = min(int(state["batch_peak"]), limit)
        never_used_capacity = max(0, limit - effective_peak)
        refill_capacity = min(
            max_buy_count,
            max(0, min(int(state["take_profit_slots"]), effective_peak - int(state["active_count"]))),
        )
        initial_capacity = min(max_buy_count, never_used_capacity)

        # If both kinds of capacity coexist, always fill genuinely never-used slots first.
        remaining_capacity = max(0, max_buy_count - initial_capacity)
        refill_capacity = min(refill_capacity, remaining_capacity)

        selected_initial = call_original(candidates, initial_capacity, args, kwargs)
        selected_tickers = {item.ticker for item in selected_initial}
        remaining_candidates = [item for item in candidates if item.ticker not in selected_tickers]

        strict_candidates = [
            item for item in remaining_candidates
            if float(getattr(item, "expect_revenue_percent", 0.0) or 0.0)
            >= custom.refill_min_expected_return
        ]

        available_cash = kwargs.get("available_cash_krw")
        if available_cash is None and len(args) > 2:
            available_cash = args[2]
        if available_cash is not None:
            planned = sum(int(getattr(item, "planned_budget_krw", 0) or 0) for item in selected_initial)
            available_cash = max(0, int(available_cash) - planned)

        selected_refill = call_original(
            strict_candidates,
            refill_capacity,
            args,
            kwargs,
            available_cash=available_cash,
        )
        selected = [*selected_initial, *selected_refill]

        refill_used = len(selected_refill)
        state["take_profit_slots"] = max(0, int(state["take_profit_slots"]) - refill_used)
        state["active_count"] += len(selected)
        state["batch_peak"] = max(int(state["batch_peak"]), int(state["active_count"]))

        limit_after = effective_limit()
        if limit_after > 0 and state["active_count"] >= limit_after:
            state["batch_full_locked"] = True
            state["batch_peak"] = limit_after

        return selected

    base.resolve_session_capital_plan = resolve_with_reset
    base.BacktestTrade = tracked_trade
    base.select_affordable_targets = select_with_batch_policy

    original_argv = sys.argv
    injected = [
        "--dynamic-expect-stop-threshold", "-0.1",
        "--dynamic-expect-stop-consecutive", "3",
        "--top-ratio", "1.0",
        "--max-prev-day-change", "0.0",
    ]
    try:
        sys.argv = [original_argv[0], *injected, *remaining]
        reserve.main()
    finally:
        sys.argv = original_argv
        base.resolve_session_capital_plan = original_resolve
        base.BacktestTrade = original_trade
        base.select_affordable_targets = original_select


if __name__ == "__main__":
    main()
