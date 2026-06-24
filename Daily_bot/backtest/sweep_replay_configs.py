from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest.replay_db_builder import resolve_replay_db_path
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
    top_ratio: float = 1.0,
    sell_tick_offset: int = 1,
) -> list[dict[str, object]]:
    resolved_db_path = resolve_replay_db_path(db_path)
    rows: list[dict[str, object]] = []
    for min_expected_return, max_spread, top_n, stop_loss_percent in product(
        min_expected_returns,
        max_spreads,
        top_ns,
        stop_loss_percents,
    ):
        trades = run_backtest(
            db_path=resolved_db_path,
            min_expected_return_percent=min_expected_return,
            max_spread_percent=max_spread,
            top_n_per_day=top_n,
            stop_loss_percent=stop_loss_percent,
            use_selected_signals=use_selected_signals,
            take_profit_percent=take_profit_percent,
            top_ratio=top_ratio,
            sell_tick_offset=sell_tick_offset,
        )
        summary = summarize_trades(trades)
        row = {
            "min_expected_return_percent": min_expected_return,
            "max_spread_percent": max_spread,
            "top_n_per_day": top_n,
            "take_profit_percent": take_profit_percent,
            "stop_loss_percent": stop_loss_percent,
            "top_ratio": top_ratio,
            "sell_tick_offset": sell_tick_offset,
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
    parser.add_argument("--logs-dir", default="")
    parser.add_argument("--min-expected-returns", default="0.2,0.25,0.3")
    parser.add_argument("--max-spreads", default="0.5,0.7")
    parser.add_argument("--top-ns", default="1,2,3")
    parser.add_argument("--top-ratio", type=float, default=1.0)
    parser.add_argument("--take-profit", type=float, default=0.25)
    parser.add_argument("--stop-losses", default="5.0,6.0,7.0")
    parser.add_argument("--sell-tick-offset", type=int, default=1)
    parser.add_argument("--out", default="Daily_bot/backtest/results/backtest_replay_sweep.csv")
    parser.add_argument("--ignore-selected-signals", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    resolved_db_path = resolve_replay_db_path(
        Path(args.db),
        Path(args.logs_dir) if args.logs_dir else None,
    )
    if resolved_db_path != Path(args.db):
        print(f"Rebuilt replay DB from logs: {resolved_db_path}")
    rows = run_sweep(
        db_path=resolved_db_path,
        min_expected_returns=_parse_number_list(args.min_expected_returns, float),
        max_spreads=_parse_number_list(args.max_spreads, float),
        top_ns=_parse_number_list(args.top_ns, int),
        take_profit_percent=args.take_profit,
        stop_loss_percents=_parse_number_list(args.stop_losses, float),
        use_selected_signals=not args.ignore_selected_signals,
        top_ratio=args.top_ratio,
        sell_tick_offset=args.sell_tick_offset,
    )
    write_csv(Path(args.out), rows)
    print(f"rows={len(rows)} wrote={args.out}")
    if rows:
        print("top_result")
        print(rows[0])
