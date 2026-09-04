from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest import replay_dynamic_expect_debounce as replay


LIVE_DYNAMIC_EXPECT_STOP_THRESHOLD = -0.1
LIVE_DYNAMIC_EXPECT_STOP_CONSECUTIVE = 3
LIVE_SLOT_POLICY_OUT = "Daily_bot/backtest/results/backtest_replay_live_slot_policy.csv"


def _apply_live_slot_policy_defaults() -> None:
    """Apply the live-equivalent defaults while preserving explicit CLI overrides."""
    argv = list(sys.argv)
    defaults = [
        ("--dynamic-expect-stop-threshold", str(LIVE_DYNAMIC_EXPECT_STOP_THRESHOLD)),
        ("--dynamic-expect-stop-consecutive", str(LIVE_DYNAMIC_EXPECT_STOP_CONSECUTIVE)),
        ("--top-ratio", "1.0"),
        ("--max-prev-day-change", "0.0"),
        ("--allow-refill-empty-slots", None),
        ("--block-stop-loss-reentry-same-day", None),
        ("--out", LIVE_SLOT_POLICY_OUT),
    ]
    for flag, value in defaults:
        if flag in argv:
            continue
        argv.append(flag)
        if value is not None:
            argv.append(value)
    sys.argv = argv


def main() -> None:
    """Replay the current live slot lifecycle policy.

    Policy per replay session:
    - profit-taking exits return their slot and may be refilled before stop-buy time;
    - each dynamic expected-return stop closes one slot for the rest of that session;
    - the stopped ticker is blocked from same-day re-entry;
    - closed-slot count resets when the next replay session begins.

    This matches the live bot's session-only slot closure policy. Explicit CLI
    arguments still override the live-equivalent defaults above.
    """

    _apply_live_slot_policy_defaults()

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
