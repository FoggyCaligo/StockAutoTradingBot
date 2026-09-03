from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest import diagnose_dynamic_expect_stop as base
from Daily_bot.backtest import replay_market_traces as replay

DEFAULT_VARIANTS = (
    ("no_dynamic_stop", None, 0),
    ("minus_0_1_immediate", -0.1, 1),
    ("zero_immediate", 0.0, 1),
    ("zero_2_consecutive", 0.0, 2),
    ("zero_3_consecutive", 0.0, 3),
)


def _first_consecutive_below(
    rows: list[tuple[replay.TraceRow, int, int, float, float]],
    threshold_percent: float,
    consecutive_count: int,
):
    streak = 0
    for item in rows:
        if item[4] <= threshold_percent:
            streak += 1
            if streak >= consecutive_count:
                return item
        else:
            streak = 0
    return None


def _load_trade_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as fp:
        return list(csv.DictReader(fp))


def _build_expected_rows_by_trade(
    db_path: Path,
    trades_path: Path,
    sell_tick_offset: int,
    orderbook_levels_per_side: int,
    bid_decay_min_weight: float,
    ask_decay_min_weight: float,
):
    traces = replay.load_traces(db_path)
    traces = replay.apply_orderbook_level_limit(
        traces,
        levels_per_side=orderbook_levels_per_side,
        sell_tick_offset=sell_tick_offset,
        bid_linear_decay_min_weight=bid_decay_min_weight,
        ask_linear_decay_min_weight=ask_decay_min_weight,
    )
    traces_by_key: dict[tuple[str, str], list[replay.TraceRow]] = defaultdict(list)
    for row in traces:
        traces_by_key[(row.session_date, row.ticker)].append(row)

    result = []
    for trade in _load_trade_rows(trades_path):
        session_date = str(trade.get("session_date") or "")
        ticker = str(trade.get("ticker") or "")
        entry_time = str(trade.get("entry_time") or "")
        exit_time = str(trade.get("exit_time") or "")
        entry_price = int(float(trade.get("entry_price") or 0))
        original_pnl = float(trade.get("pnl_percent") or 0.0)
        entry_dt = base._parse_time(entry_time)
        exit_dt = base._parse_time(exit_time)

        position_rows: list[replay.TraceRow] = []
        for row in traces_by_key.get((session_date, ticker), []):
            try:
                row_dt = base._parse_time(row.created_at)
            except ValueError:
                continue
            if entry_dt < row_dt <= exit_dt:
                position_rows.append(row)

        expected_rows = base._ordered_expected_rows(position_rows, entry_price, sell_tick_offset)
        result.append((trade, entry_price, original_pnl, expected_rows))
    return result


def run_variants(
    db_path: Path,
    trades_path: Path,
    sell_tick_offset: int,
    orderbook_levels_per_side: int,
    bid_decay_min_weight: float,
    ask_decay_min_weight: float,
) -> list[dict[str, object]]:
    prepared = _build_expected_rows_by_trade(
        db_path=db_path,
        trades_path=trades_path,
        sell_tick_offset=sell_tick_offset,
        orderbook_levels_per_side=orderbook_levels_per_side,
        bid_decay_min_weight=bid_decay_min_weight,
        ask_decay_min_weight=ask_decay_min_weight,
    )

    rows: list[dict[str, object]] = []
    for label, threshold, consecutive_count in DEFAULT_VARIANTS:
        for trade, entry_price, original_pnl, expected_rows in prepared:
            trigger = None
            if threshold is not None:
                trigger = _first_consecutive_below(expected_rows, threshold, consecutive_count)

            trigger_current_price = trigger[1] if trigger else 0
            approx_exit_pnl = (
                (trigger_current_price - entry_price) / entry_price * 100
                if trigger_current_price > 0 and entry_price > 0
                else original_pnl
            )
            rows.append(
                {
                    "strategy": label,
                    "threshold_percent": "" if threshold is None else threshold,
                    "consecutive_count": consecutive_count,
                    "session_date": trade.get("session_date", ""),
                    "ticker": trade.get("ticker", ""),
                    "entry_time": trade.get("entry_time", ""),
                    "original_exit_time": trade.get("exit_time", ""),
                    "original_exit_reason": trade.get("exit_reason", ""),
                    "original_pnl_percent": round(original_pnl, 4),
                    "triggered": 1 if trigger else 0,
                    "trigger_time": trigger[0].created_at if trigger else "",
                    "trigger_current_price": trigger[1] if trigger else "",
                    "trigger_expect_price": trigger[0].expect_price if trigger else "",
                    "trigger_expected_sell_price": trigger[2] if trigger else "",
                    "trigger_expected_return_vs_current_percent": round(trigger[4], 4) if trigger else "",
                    "approx_exit_pnl_percent": round(approx_exit_pnl, 4),
                    "approx_pnl_change_vs_original_percent_point": round(approx_exit_pnl - original_pnl, 4),
                }
            )
    return rows


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows: list[dict[str, object]]) -> None:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["strategy"])].append(row)

    print("debounce_comparison:")
    for label, _, _ in DEFAULT_VARIANTS:
        strategy_rows = grouped.get(label, [])
        if not strategy_rows:
            continue
        triggered = [row for row in strategy_rows if int(row["triggered"] or 0) == 1]
        original_losses = [row for row in strategy_rows if float(row["original_pnl_percent"] or 0) < 0]
        caught_losses = [row for row in triggered if float(row["original_pnl_percent"] or 0) < 0]
        cut_winners = [row for row in triggered if float(row["original_pnl_percent"] or 0) > 0]
        improved = [row for row in triggered if float(row["approx_pnl_change_vs_original_percent_point"] or 0) > 0]
        pnls = [float(row["approx_exit_pnl_percent"] or 0) for row in strategy_rows]
        print(
            f"  {label}: triggered={len(triggered)}/{len(strategy_rows)} "
            f"caught_losses={len(caught_losses)}/{len(original_losses)} "
            f"cut_winners={len(cut_winners)} improved={len(improved)} "
            f"avg_pnl={sum(pnls) / len(pnls):.4f}% "
            f"sum_pnl={sum(pnls):.4f}% worst_pnl={min(pnls):.4f}%"
        )


def parse_args():
    cfg = replay._load_backtest_default_config(ROOT.parent / "config/settings.yaml")
    strategy_cfg = cfg.get("strategy", {})
    parser = argparse.ArgumentParser(
        description="Compare immediate and consecutive-refresh dynamic expected-return stop rules."
    )
    parser.add_argument(
        "--db",
        default="Daily_bot/backtest/cache/rebuild_from_logs_replay_from_logs.sqlite3",
    )
    parser.add_argument("--trades", default="")
    parser.add_argument(
        "--out",
        default="Daily_bot/backtest/results/dynamic_expect_stop_debounce_sweep.csv",
    )
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    trades_path = base._resolve_trades_path(args.trades)
    print(f"using_trades={trades_path}")
    rows = run_variants(
        db_path=Path(args.db),
        trades_path=trades_path,
        sell_tick_offset=args.sell_tick_offset,
        orderbook_levels_per_side=args.orderbook_levels_per_side,
        bid_decay_min_weight=args.orderbook_bid_linear_decay_min_weight,
        ask_decay_min_weight=args.orderbook_ask_linear_decay_min_weight,
    )
    write_rows(Path(args.out), rows)
    print_summary(rows)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
