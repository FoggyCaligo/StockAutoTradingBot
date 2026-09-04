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
    """Replay the live-aligned slot policy with a stricter threshold on TP refills only.

    Never-used slots keep the normal entry threshold. Only slots returned by
    ``take_profit`` require ``--refill-min-expected-return``. Dynamic-stop slots
    remain reserved for the rest of the session through the reserve runner.

    This module is backtest-only and does not alter live trading behavior.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--refill-min-expected-return", type=float, required=True)
    custom, remaining = parser.parse_known_args()

    base = reserve.replay.base
    state = {"take_profit_slots": 0}

    original_trade = base.BacktestTrade
    original_select = base.select_affordable_targets
    original_resolve = base.resolve_session_capital_plan

    def resolve_with_reset(*args, **kwargs):
        state["take_profit_slots"] = 0
        return original_resolve(*args, **kwargs)

    def tracked_trade(*args, **kwargs):
        trade = original_trade(*args, **kwargs)
        if trade.exit_reason == "take_profit":
            state["take_profit_slots"] += 1
        return trade

    def select_with_refill_threshold(*args, **kwargs):
        candidates = list(args[0] if args else kwargs.get("candidates", []))
        max_buy_count = int(kwargs.get("max_buy_count", args[1] if len(args) > 1 else 0) or 0)
        if max_buy_count <= 0 or not candidates:
            return original_select(*args, **kwargs)

        refill_capacity = min(max_buy_count, max(0, int(state["take_profit_slots"])))
        initial_capacity = max(0, max_buy_count - refill_capacity)

        def call_original(pool, count, available_cash=None):
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

        selected_initial = call_original(candidates, initial_capacity)
        selected_tickers = {item.ticker for item in selected_initial}
        remaining = [item for item in candidates if item.ticker not in selected_tickers]

        strict_candidates = [
            item for item in remaining
            if float(getattr(item, "expect_revenue_percent", 0.0) or 0.0)
            >= custom.refill_min_expected_return
        ]

        available_cash = kwargs.get("available_cash_krw")
        if available_cash is None and len(args) > 2:
            available_cash = args[2]
        if available_cash is not None:
            planned = sum(int(getattr(item, "planned_budget_krw", 0) or 0) for item in selected_initial)
            available_cash = max(0, int(available_cash) - planned)

        selected_refill = call_original(strict_candidates, refill_capacity, available_cash)
        state["take_profit_slots"] = max(0, state["take_profit_slots"] - len(selected_refill))
        return [*selected_initial, *selected_refill]

    base.resolve_session_capital_plan = resolve_with_reset
    base.BacktestTrade = tracked_trade
    base.select_affordable_targets = select_with_refill_threshold

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
