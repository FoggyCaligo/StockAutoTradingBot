from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest import replay_dynamic_expect_debounce_no_refill as live_replay


def main() -> None:
    """Replay current live dynamic-stop behavior with two entry filters disabled.

    Experiment-only overrides:
    - remove the top-ratio cutoff by using the full ranked candidate list
    - disable the previous-day change upper bound

    The minimum expected-return threshold remains sourced from settings.yaml.
    """
    original_argv = sys.argv
    injected = [
        "--dynamic-expect-stop-threshold", "-0.1",
        "--dynamic-expect-stop-consecutive", "3",
        "--top-ratio", "1.0",
        "--max-prev-day-change", "0.0",
        "--out", "Daily_bot/backtest/results/backtest_live_no_top_cut_no_prevday_cap.csv",
    ]
    try:
        sys.argv = [original_argv[0], *injected, *original_argv[1:]]
        live_replay.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
