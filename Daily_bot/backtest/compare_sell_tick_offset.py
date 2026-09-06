from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPLAY_SCRIPT = ROOT / "replay_refill_threshold.py"
DEFAULT_OUT_DIR = ROOT / "results" / "sell_tick_offset_ab"
DEFAULT_REFILL_MIN_EXPECTED_RETURN = 0.90


@dataclass
class ReplaySummary:
    offset: int
    trades: int
    wins: int
    losses: int
    win_rate_percent: float
    avg_pnl_percent: float
    summed_pnl_percent: float
    exit_reasons: dict[str, int]
    stdout: str


def _strip_conflicting_args(args: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in {"--sell-tick-offset", "--out", "--refill-min-expected-return"}:
            skip_next = True
            continue
        if (
            arg.startswith("--sell-tick-offset=")
            or arg.startswith("--out=")
            or arg.startswith("--refill-min-expected-return=")
        ):
            continue
        cleaned.append(arg)
    return cleaned


def _parse_summary(offset: int, stdout: str) -> ReplaySummary:
    headline = re.search(
        r"trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)\s+win_rate=([-+\d.]+)%",
        stdout,
    )
    pnl = re.search(
        r"avg_pnl=([-+\d.]+)%\s+summed_pnl=([-+\d.]+)%",
        stdout,
    )
    if headline is None or pnl is None:
        raise RuntimeError(
            f"Could not parse replay summary for sell_tick_offset={offset}.\n"
            f"Replay output follows:\n{stdout}"
        )

    exit_reasons: dict[str, int] = {}
    in_exit_reasons = False
    for line in stdout.splitlines():
        if line.strip() == "exit_reasons:":
            in_exit_reasons = True
            continue
        if in_exit_reasons:
            match = re.match(r"\s{2}([^:]+):\s*(\d+)\s*$", line)
            if match:
                exit_reasons[match.group(1).strip()] = int(match.group(2))
                continue
            if line and not line.startswith("  "):
                break

    return ReplaySummary(
        offset=offset,
        trades=int(headline.group(1)),
        wins=int(headline.group(2)),
        losses=int(headline.group(3)),
        win_rate_percent=float(headline.group(4)),
        avg_pnl_percent=float(pnl.group(1)),
        summed_pnl_percent=float(pnl.group(2)),
        exit_reasons=exit_reasons,
        stdout=stdout,
    )


def _run_replay(
    offset: int,
    passthrough_args: list[str],
    out_dir: Path,
    refill_min_expected_return: float,
) -> ReplaySummary:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"backtest_sell_tick_offset_{offset}.csv"
    command = [
        sys.executable,
        str(REPLAY_SCRIPT),
        "--refill-min-expected-return",
        str(refill_min_expected_return),
        *passthrough_args,
        "--sell-tick-offset",
        str(offset),
        "--out",
        str(out_path),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT.parent.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Replay failed for sell_tick_offset={offset} with exit code {completed.returncode}.\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return _parse_summary(offset, completed.stdout)


def _fmt_delta(value: float, digits: int = 4) -> str:
    return f"{value:+.{digits}f}"


def _print_comparison(baseline: ReplaySummary, exact: ReplaySummary) -> None:
    print("\n=== Sell target A/B comparison (live-equivalent replay) ===")
    print("baseline: sell_tick_offset=1 (predicted price - 1 tick)")
    print("variant : sell_tick_offset=0 (predicted price)")
    print()
    print(f"{'metric':<24} {'offset=1':>14} {'offset=0':>14} {'delta(0-1)':>14}")
    print("-" * 70)
    rows = [
        ("trades", baseline.trades, exact.trades, exact.trades - baseline.trades, "int"),
        ("wins", baseline.wins, exact.wins, exact.wins - baseline.wins, "int"),
        ("losses", baseline.losses, exact.losses, exact.losses - baseline.losses, "int"),
        (
            "win_rate_percent",
            baseline.win_rate_percent,
            exact.win_rate_percent,
            exact.win_rate_percent - baseline.win_rate_percent,
            "pct2",
        ),
        (
            "avg_pnl_percent",
            baseline.avg_pnl_percent,
            exact.avg_pnl_percent,
            exact.avg_pnl_percent - baseline.avg_pnl_percent,
            "pct4",
        ),
        (
            "summed_pnl_percent",
            baseline.summed_pnl_percent,
            exact.summed_pnl_percent,
            exact.summed_pnl_percent - baseline.summed_pnl_percent,
            "pct4",
        ),
    ]
    for name, left, right, delta, kind in rows:
        if kind == "int":
            print(f"{name:<24} {left:>14d} {right:>14d} {delta:>+14d}")
        elif kind == "pct2":
            print(f"{name:<24} {left:>13.2f}% {right:>13.2f}% {_fmt_delta(delta, 2):>13}%")
        else:
            print(f"{name:<24} {left:>13.4f}% {right:>13.4f}% {_fmt_delta(delta, 4):>13}%")

    all_reasons = sorted(set(baseline.exit_reasons) | set(exact.exit_reasons))
    if all_reasons:
        print("\nexit reasons")
        print(f"{'reason':<32} {'offset=1':>10} {'offset=0':>10} {'delta':>10}")
        print("-" * 66)
        for reason in all_reasons:
            left = baseline.exit_reasons.get(reason, 0)
            right = exact.exit_reasons.get(reason, 0)
            print(f"{reason:<32} {left:>10d} {right:>10d} {right-left:>+10d}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the live-equivalent refill replay twice with identical settings and compare "
            "sell_tick_offset=1 (predicted price - 1 tick) vs 0 (predicted price)."
        ),
        add_help=True,
    )
    parser.add_argument(
        "--ab-out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Directory for the two replay CSV result sets.",
    )
    parser.add_argument(
        "--refill-min-expected-return",
        type=float,
        default=DEFAULT_REFILL_MIN_EXPECTED_RETURN,
        help="Expected-return threshold for slots returned by take-profit.",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="Print the full stdout from both replay runs before the comparison table.",
    )
    known, passthrough = parser.parse_known_args()
    passthrough = _strip_conflicting_args(passthrough)

    baseline = _run_replay(
        1,
        passthrough,
        Path(known.ab_out_dir),
        known.refill_min_expected_return,
    )
    exact = _run_replay(
        0,
        passthrough,
        Path(known.ab_out_dir),
        known.refill_min_expected_return,
    )

    if known.show_raw:
        print("=== raw: sell_tick_offset=1 ===")
        print(baseline.stdout.rstrip())
        print("\n=== raw: sell_tick_offset=0 ===")
        print(exact.stdout.rstrip())

    _print_comparison(baseline, exact)
    print(f"\nresults written under: {Path(known.ab_out_dir)}")


if __name__ == "__main__":
    main()
