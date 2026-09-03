from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest import replay_dynamic_expect_debounce as replay


def main() -> None:
    """Replay debounced dynamic stops while reserving stopped slots for the day.

    Each ``dynamic_expect_stop`` permanently reduces that session's effective
    position limit by one. Profit-taking exits do not reduce the limit, so those
    vacated slots may still be refilled. The reservation resets next session.

    This is backtest-only and leaves live trading behavior unchanged.
    """

    base = replay.base
    state = {
        "dynamic_stop_count": 0,
        "current_refill_capacity": 0,
    }

    original_resolve_session_capital_plan = base.resolve_session_capital_plan
    original_refill_is_allowed = base._refill_is_allowed
    original_select_affordable_targets = base.select_affordable_targets
    original_backtest_trade = base.BacktestTrade

    def resolve_session_capital_plan_with_reset(*args, **kwargs):
        state["dynamic_stop_count"] = 0
        state["current_refill_capacity"] = 0
        return original_resolve_session_capital_plan(*args, **kwargs)

    def refill_is_allowed_with_reserved_stopped_slots(*args, **kwargs):
        active_count = int(kwargs.get("active_position_count", args[0] if args else 0) or 0)
        position_limit = int(kwargs.get("position_limit", args[1] if len(args) > 1 else 0) or 0)
        adjusted_limit = max(0, position_limit - state["dynamic_stop_count"])
        state["current_refill_capacity"] = max(0, adjusted_limit - active_count)

        patched_kwargs = dict(kwargs)
        if "position_limit" in patched_kwargs:
            patched_kwargs["position_limit"] = adjusted_limit
            return original_refill_is_allowed(*args, **patched_kwargs)

        patched_args = list(args)
        if len(patched_args) > 1:
            patched_args[1] = adjusted_limit
            return original_refill_is_allowed(*patched_args, **kwargs)

        return original_refill_is_allowed(
            active_position_count=active_count,
            position_limit=adjusted_limit,
            allow_refill_empty_slots=bool(kwargs.get("allow_refill_empty_slots", True)),
            refill_min_empty_fraction=float(kwargs.get("refill_min_empty_fraction", 0.0) or 0.0),
        )

    def select_affordable_targets_with_reserved_stopped_slots(*args, **kwargs):
        capacity = max(0, int(state["current_refill_capacity"]))
        if capacity <= 0:
            return []

        patched_kwargs = dict(kwargs)
        if "max_buy_count" in patched_kwargs:
            configured = int(patched_kwargs.get("max_buy_count", 0) or 0)
            patched_kwargs["max_buy_count"] = capacity if configured <= 0 else min(configured, capacity)
            return original_select_affordable_targets(*args, **patched_kwargs)

        patched_args = list(args)
        if len(patched_args) > 1:
            configured = int(patched_args[1] or 0)
            patched_args[1] = capacity if configured <= 0 else min(configured, capacity)
            return original_select_affordable_targets(*patched_args, **kwargs)

        return original_select_affordable_targets(*args, **kwargs)

    def tracked_backtest_trade(*args, **kwargs):
        trade = original_backtest_trade(*args, **kwargs)
        if trade.exit_reason == "dynamic_expect_stop":
            state["dynamic_stop_count"] += 1
        return trade

    base.resolve_session_capital_plan = resolve_session_capital_plan_with_reset
    base._refill_is_allowed = refill_is_allowed_with_reserved_stopped_slots
    base.select_affordable_targets = select_affordable_targets_with_reserved_stopped_slots
    base.BacktestTrade = tracked_backtest_trade

    try:
        replay.main()
    finally:
        base.resolve_session_capital_plan = original_resolve_session_capital_plan
        base._refill_is_allowed = original_refill_is_allowed
        base.select_affordable_targets = original_select_affordable_targets
        base.BacktestTrade = original_backtest_trade


if __name__ == "__main__":
    main()
