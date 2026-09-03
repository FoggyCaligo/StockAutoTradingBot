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
LIVE_EQUIVALENT_OUT = "Daily_bot/backtest/results/backtest_replay_live_current.csv"


def _apply_live_equivalent_defaults() -> None:
    argv = list(sys.argv)
    if "--dynamic-expect-stop-threshold" not in argv:
        argv.extend(["--dynamic-expect-stop-threshold", str(LIVE_DYNAMIC_EXPECT_STOP_THRESHOLD)])
    if "--dynamic-expect-stop-consecutive" not in argv:
        argv.extend(["--dynamic-expect-stop-consecutive", str(LIVE_DYNAMIC_EXPECT_STOP_CONSECUTIVE)])
    if "--out" not in argv:
        argv.extend(["--out", LIVE_EQUIVALENT_OUT])
    sys.argv = argv


def main() -> None:
    """Run the current live-equivalent debounced dynamic-expectation replay.

    Strategy, market-time, universe, and risk defaults come from settings.yaml.
    The live dynamic stop defaults to -0.1% for 3 consecutive observations.
    After the first ``dynamic_expect_stop`` exit in a session, additional buys are
    blocked for the remainder of that session. Existing positions continue to be
    managed normally. The block is reset when the next replay session starts.

    Explicit CLI arguments still override the live-equivalent dynamic-stop defaults.
    This wrapper is backtest-only and leaves live trading behavior unchanged.
    """

    _apply_live_equivalent_defaults()

    base = replay.base
    state = {"dynamic_stop_seen": False}

    original_resolve_session_capital_plan = base.resolve_session_capital_plan
    original_refill_is_allowed = base._refill_is_allowed
    original_backtest_trade = base.BacktestTrade

    def resolve_session_capital_plan_with_reset(*args, **kwargs):
        state["dynamic_stop_seen"] = False
        return original_resolve_session_capital_plan(*args, **kwargs)

    def refill_is_allowed_without_dynamic_refill(*args, **kwargs):
        if state["dynamic_stop_seen"]:
            return False
        return original_refill_is_allowed(*args, **kwargs)

    def tracked_backtest_trade(*args, **kwargs):
        trade = original_backtest_trade(*args, **kwargs)
        if trade.exit_reason == "dynamic_expect_stop":
            state["dynamic_stop_seen"] = True
        return trade

    base.resolve_session_capital_plan = resolve_session_capital_plan_with_reset
    base._refill_is_allowed = refill_is_allowed_without_dynamic_refill
    base.BacktestTrade = tracked_backtest_trade

    try:
        replay.main()
    finally:
        base.resolve_session_capital_plan = original_resolve_session_capital_plan
        base._refill_is_allowed = original_refill_is_allowed
        base.BacktestTrade = original_backtest_trade


if __name__ == "__main__":
    main()
