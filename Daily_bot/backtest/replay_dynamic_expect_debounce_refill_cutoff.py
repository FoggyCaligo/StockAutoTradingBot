from __future__ import annotations

import sys
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest import replay_dynamic_expect_debounce as replay


REFILL_CUTOFF_TIME = time(10, 15)


def main() -> None:
    """Replay dynamic-expectation stops with refill allowed only through 10:15.

    The normal buy window remains unchanged. The 10:15 cutoff applies only after
    at least one position has already exited during the session, so initial entries
    can still occur until the configured normal stop-buy time. Same-ticker re-entry
    remains allowed because the same-day stop-loss re-entry block is not enabled.
    """

    base = replay.base
    state = {
        "completed_trade_seen": False,
        "current_created_at": None,
    }

    original_resolve_session_capital_plan = base.resolve_session_capital_plan
    original_refill_is_allowed = base._refill_is_allowed
    original_is_within_buy_window = base._is_within_buy_window
    original_backtest_trade = base.BacktestTrade

    def resolve_session_capital_plan_with_reset(*args, **kwargs):
        state["completed_trade_seen"] = False
        state["current_created_at"] = None
        return original_resolve_session_capital_plan(*args, **kwargs)

    def tracked_is_within_buy_window(created_at, start_buy_time, stop_buy_time):
        state["current_created_at"] = created_at
        return original_is_within_buy_window(created_at, start_buy_time, stop_buy_time)

    def refill_is_allowed_with_cutoff(*args, **kwargs):
        if state["completed_trade_seen"] and state["current_created_at"]:
            current_time = base._parse_timestamp(state["current_created_at"]).time()
            if current_time > REFILL_CUTOFF_TIME:
                return False
        return original_refill_is_allowed(*args, **kwargs)

    def tracked_backtest_trade(*args, **kwargs):
        trade = original_backtest_trade(*args, **kwargs)
        state["completed_trade_seen"] = True
        return trade

    base.resolve_session_capital_plan = resolve_session_capital_plan_with_reset
    base._is_within_buy_window = tracked_is_within_buy_window
    base._refill_is_allowed = refill_is_allowed_with_cutoff
    base.BacktestTrade = tracked_backtest_trade

    try:
        replay.main()
    finally:
        base.resolve_session_capital_plan = original_resolve_session_capital_plan
        base._is_within_buy_window = original_is_within_buy_window
        base._refill_is_allowed = original_refill_is_allowed
        base.BacktestTrade = original_backtest_trade


if __name__ == "__main__":
    main()
