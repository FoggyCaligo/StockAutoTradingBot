from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from itertools import product
from pathlib import Path

from Daily_bot.backtest.replay_market_traces import run_backtest, summarize_trades


def _parse_number_list(raw: str, cast):
    values = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        values.append(cast(item))
    if not values:
        raise ValueError(f"No values parsed from: {raw}")
    return values


def run_sweep(
    db_path: Path,
    min_expected_returns: list[float],
    max_spreads: list[float],
    top_ns: list[int],
    take_profit_percent: float,
    stop_loss_percents: list[float],
    use_selected_signals: bool,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for min_expected_return, max_spread, top_n, stop_loss_percent in product(
        min_expected_returns,
        max_spreads,
        top_ns,
        stop_loss_percents,
    ):
        trades = run_backtest(
            db_path=db_path,
            min_expected_return_percent=min_expected_return,
            max_spread_percent=max_spread,
            top_n_per_day=top_n,
            take_profit_percent=take_profit_percent,
            stop_loss_percent=stop_loss_percent,
            use_selected_signals=use_selected_signals,
        )
        summary = summarize_trades(trades)
        row = {
            "min_expected_return_percent": min_expected_return,
            "max_spread_percent": max_spread,
            "top_n_per_day": top_n,
            "take_profit_percent": take_profit_percent,
            "stop_loss_percent": stop_loss_percent,
            "use_selected_signals": int(use_selected_signals),
            **asdict(summary),
        }
        rows.append(row)
    rows.sort(
        key=lambda item: (
            -float(item["total_pnl_percent"]),
            -float(item["win_rate_percent"]),
            -int(item["trades"]),
        )
    )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(description="Sweep Daily_bot replay backtest settings.")
    parser.add_argument("--db", default="Daily_bot/bot.sqlite3")
    parser.add_argument("--min-expected-returns", default="0.2,0.25,0.3")
    parser.add_argument("--max-spreads", default="0.5,0.7")
    parser.add_argument("--top-ns", default="1,2,3")
    parser.add_argument("--take-profit", type=float, default=0.25)
    parser.add_argument("--stop-losses", default="5.0,6.0,7.0")
    parser.add_argument("--out", default="Daily_bot/logs/backtest_replay_sweep.csv")
    parser.add_argument("--ignore-selected-signals", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    rows = run_sweep(
        db_path=Path(args.db),
        min_expected_returns=_parse_number_list(args.min_expected_returns, float),
        max_spreads=_parse_number_list(args.max_spreads, float),
        top_ns=_parse_number_list(args.top_ns, int),
        take_profit_percent=args.take_profit,
        stop_loss_percents=_parse_number_list(args.stop_losses, float),
        use_selected_signals=not args.ignore_selected_signals,
    )
    write_csv(Path(args.out), rows)
    print(f"rows={len(rows)} wrote={args.out}")
    if rows:
        print("top_result")
        print(rows[0])
