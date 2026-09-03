from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest import replay_market_traces as replay


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).strip().replace(" ", "T"))


def _load_trades(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as fp:
        return list(csv.DictReader(fp))


def _phase_counts_text(rows: list[replay.TraceRow]) -> str:
    counts = Counter(row.phase for row in rows)
    return ",".join(f"{phase}:{counts[phase]}" for phase in sorted(counts))


def diagnose(
    db_path: Path,
    trades_path: Path,
    out_path: Path,
    sell_tick_offset: int,
    orderbook_levels_per_side: int,
    bid_decay_min_weight: float,
    ask_decay_min_weight: float,
) -> list[dict[str, object]]:
    traces = replay.load_traces(db_path)
    traces = replay.apply_orderbook_level_limit(
        traces,
        levels_per_side=orderbook_levels_per_side,
        sell_tick_offset=sell_tick_offset,
        bid_linear_decay_min_weight=bid_decay_min_weight,
        ask_linear_decay_min_weight=ask_decay_min_weight,
    )
    traces_by_key: dict[tuple[str, str], list[replay.TraceRow]] = {}
    for row in traces:
        traces_by_key.setdefault((row.session_date, row.ticker), []).append(row)

    diagnostics: list[dict[str, object]] = []
    for trade in _load_trades(trades_path):
        session_date = str(trade.get("session_date") or "")
        ticker = str(trade.get("ticker") or "")
        entry_time = str(trade.get("entry_time") or "")
        exit_time = str(trade.get("exit_time") or "")
        entry_price = int(float(trade.get("entry_price") or 0))
        entry_dt = _parse_time(entry_time)
        exit_dt = _parse_time(exit_time)

        position_rows = []
        for row in traces_by_key.get((session_date, ticker), []):
            try:
                row_dt = _parse_time(row.created_at)
            except ValueError:
                continue
            if entry_dt < row_dt <= exit_dt:
                position_rows.append(row)

        expected_sell_rows: list[tuple[replay.TraceRow, int, float]] = []
        for row in position_rows:
            if row.expect_price <= 0 or entry_price <= 0:
                continue
            expected_sell_price = replay.calc_target_sell_price(row.expect_price, sell_tick_offset)
            expected_return_vs_entry = (expected_sell_price - entry_price) / entry_price * 100
            expected_sell_rows.append((row, expected_sell_price, expected_return_vs_entry))

        first_negative = next(
            (
                (row.created_at, expected_sell_price, expected_return_vs_entry)
                for row, expected_sell_price, expected_return_vs_entry in expected_sell_rows
                if expected_sell_price < entry_price
            ),
            None,
        )
        min_expected = min(expected_sell_rows, key=lambda item: item[1]) if expected_sell_rows else None
        intervals = []
        sorted_rows = sorted(position_rows, key=lambda item: item.created_at)
        for prev_row, next_row in zip(sorted_rows, sorted_rows[1:]):
            try:
                intervals.append((_parse_time(next_row.created_at) - _parse_time(prev_row.created_at)).total_seconds())
            except ValueError:
                continue

        diagnostics.append(
            {
                "session_date": session_date,
                "ticker": ticker,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "exit_reason": trade.get("exit_reason", ""),
                "trade_pnl_percent": trade.get("pnl_percent", ""),
                "entry_price": entry_price,
                "refresh_count_after_entry": len(position_rows),
                "refresh_phase_counts": _phase_counts_text(position_rows),
                "median_refresh_interval_seconds": round(median(intervals), 2) if intervals else "",
                "min_current_price": min(
                    ((row.current_price or row.price) for row in position_rows if (row.current_price or row.price) > 0),
                    default="",
                ),
                "min_refreshed_expect_price": min((row.expect_price for row in position_rows if row.expect_price > 0), default=""),
                "min_expected_sell_price": min_expected[1] if min_expected else "",
                "min_expected_return_vs_entry_percent": round(min_expected[2], 4) if min_expected else "",
                "first_negative_expect_time": first_negative[0] if first_negative else "",
                "first_negative_expected_sell_price": first_negative[1] if first_negative else "",
                "first_negative_expected_return_vs_entry_percent": round(first_negative[2], 4) if first_negative else "",
            }
        )

    fieldnames = list(diagnostics[0].keys()) if diagnostics else [
        "session_date",
        "ticker",
        "entry_time",
        "exit_time",
        "exit_reason",
        "trade_pnl_percent",
        "entry_price",
        "refresh_count_after_entry",
        "refresh_phase_counts",
        "median_refresh_interval_seconds",
        "min_current_price",
        "min_refreshed_expect_price",
        "min_expected_sell_price",
        "min_expected_return_vs_entry_percent",
        "first_negative_expect_time",
        "first_negative_expected_sell_price",
        "first_negative_expected_return_vs_entry_percent",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(diagnostics)
    return diagnostics


def main() -> None:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
    config_args, remaining = config_parser.parse_known_args()
    cfg = replay._load_backtest_default_config(config_args.config)
    strategy_cfg = cfg.get("strategy", {})

    parser = argparse.ArgumentParser(
        description="Diagnose refresh coverage and expected-price behavior for dynamic expected-loss stops.",
        parents=[config_parser],
    )
    parser.add_argument("--db", default="Daily_bot/bot.sqlite3")
    parser.add_argument("--trades", default="Daily_bot/backtest/results/backtest_replay.csv")
    parser.add_argument("--out", default="Daily_bot/backtest/results/dynamic_expect_stop_diagnostics.csv")
    parser.add_argument("--sell-tick-offset", type=int, default=int(strategy_cfg.get("sell_tick_offset", 1) or 1))
    parser.add_argument("--orderbook-levels-per-side", type=int, default=10)
    parser.add_argument(
        "--orderbook-bid-linear-decay-min-weight",
        type=float,
        default=float(strategy_cfg.get("orderbook_bid_linear_decay_min_weight", 1.0) or 0.0),
    )
    parser.add_argument(
        "--orderbook-ask-linear-decay-min-weight",
        type=float,
        default=float(strategy_cfg.get("orderbook_ask_linear_decay_min_weight", 1.0) or 0.0),
    )
    args = parser.parse_args(remaining)

    diagnostics = diagnose(
        db_path=Path(args.db),
        trades_path=Path(args.trades),
        out_path=Path(args.out),
        sell_tick_offset=args.sell_tick_offset,
        orderbook_levels_per_side=args.orderbook_levels_per_side,
        bid_decay_min_weight=args.orderbook_bid_linear_decay_min_weight,
        ask_decay_min_weight=args.orderbook_ask_linear_decay_min_weight,
    )

    negative_signal_count = sum(1 for row in diagnostics if row["first_negative_expect_time"])
    zero_refresh_count = sum(1 for row in diagnostics if int(row["refresh_count_after_entry"] or 0) == 0)
    print(f"diagnosed_trades={len(diagnostics)}")
    print(f"trades_with_negative_expectation={negative_signal_count}")
    print(f"trades_with_zero_refreshes={zero_refresh_count}")
    print(f"wrote {args.out}")
    print("loss_trades:")
    for row in diagnostics:
        try:
            pnl = float(row["trade_pnl_percent"] or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        if pnl >= 0:
            continue
        print(
            f"  {row['session_date']} {row['ticker']} pnl={pnl:.4f}% "
            f"refreshes={row['refresh_count_after_entry']} "
            f"median_interval={row['median_refresh_interval_seconds']}s "
            f"min_expected_vs_entry={row['min_expected_return_vs_entry_percent']}% "
            f"first_negative={row['first_negative_expect_time'] or '-'}"
        )


if __name__ == "__main__":
    main()
