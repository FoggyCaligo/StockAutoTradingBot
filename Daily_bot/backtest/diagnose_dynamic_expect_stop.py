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

DEFAULT_SWEEP_THRESHOLDS = (-0.1, -0.2, -0.3, -0.4, -0.5)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).strip().replace(" ", "T"))


def _load_trades(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as fp:
        return list(csv.DictReader(fp))


def _looks_like_trade_csv(path: Path) -> bool:
    name = path.name.lower()
    if any(token in name for token in ("daily_rev", "audit", "diagnostic", "threshold_sweep")):
        return False
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            fields = set(next(csv.DictReader(fp), {}).keys())
    except (OSError, UnicodeError):
        return False
    return {"session_date", "ticker", "entry_time", "exit_time", "entry_price", "pnl_percent"}.issubset(fields)


def _resolve_trades_path(raw_path: str) -> Path:
    if raw_path:
        explicit = Path(raw_path)
        if explicit.exists():
            return explicit
        raise FileNotFoundError(f"Trade CSV not found: {explicit}")

    candidates = [
        Path("Daily_bot/backtest/results/backtest_replay.csv"),
        Path("backtest_replay.csv"),
        Path("Daily_bot/backtest/results/dynamic_expect_stop.csv"),
        Path("dynamic_expect_stop.csv"),
    ]
    for candidate in candidates:
        if candidate.exists() and _looks_like_trade_csv(candidate):
            return candidate

    discovered: list[Path] = []
    for directory in (Path("Daily_bot/backtest/results"), Path.cwd()):
        if not directory.exists():
            continue
        for candidate in directory.glob("*.csv"):
            if _looks_like_trade_csv(candidate):
                discovered.append(candidate)
    if discovered:
        return max(discovered, key=lambda path: path.stat().st_mtime)

    raise FileNotFoundError(
        "No replay trade CSV found. Run replay_market_traces.py first, or pass --trades <path>."
    )


def _phase_counts_text(rows: list[replay.TraceRow]) -> str:
    counts = Counter(row.phase for row in rows)
    return ",".join(f"{phase}:{counts[phase]}" for phase in sorted(counts))


def _ordered_expected_rows(
    position_rows: list[replay.TraceRow],
    entry_price: int,
    sell_tick_offset: int,
) -> list[tuple[replay.TraceRow, int, int, float, float]]:
    result: list[tuple[replay.TraceRow, int, int, float, float]] = []
    for row in position_rows:
        current_price = int((row.current_price or row.price) or 0)
        if row.expect_price <= 0 or current_price <= 0 or entry_price <= 0:
            continue
        expected_sell_price = replay.calc_target_sell_price(row.expect_price, sell_tick_offset)
        expected_return_vs_entry = (expected_sell_price - entry_price) / entry_price * 100
        expected_return_vs_current = (expected_sell_price - current_price) / current_price * 100
        result.append(
            (
                row,
                current_price,
                expected_sell_price,
                expected_return_vs_entry,
                expected_return_vs_current,
            )
        )
    return sorted(result, key=lambda item: _parse_time(item[0].created_at))


def _first_below_current_threshold(
    rows: list[tuple[replay.TraceRow, int, int, float, float]],
    threshold_percent: float,
):
    for item in rows:
        if item[4] <= threshold_percent:
            return item
    return None


def _write_rows(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def diagnose(
    db_path: Path,
    trades_path: Path,
    out_path: Path,
    sweep_out_path: Path,
    sell_tick_offset: int,
    orderbook_levels_per_side: int,
    bid_decay_min_weight: float,
    ask_decay_min_weight: float,
    trigger_threshold_percent: float,
    sweep_thresholds: list[float],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
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
    sweep_rows: list[dict[str, object]] = []
    for trade in _load_trades(trades_path):
        session_date = str(trade.get("session_date") or "")
        ticker = str(trade.get("ticker") or "")
        entry_time = str(trade.get("entry_time") or "")
        exit_time = str(trade.get("exit_time") or "")
        entry_price = int(float(trade.get("entry_price") or 0))
        original_pnl = float(trade.get("pnl_percent") or 0.0)
        entry_dt = _parse_time(entry_time)
        exit_dt = _parse_time(exit_time)

        position_rows: list[replay.TraceRow] = []
        for row in traces_by_key.get((session_date, ticker), []):
            try:
                row_dt = _parse_time(row.created_at)
            except ValueError:
                continue
            if entry_dt < row_dt <= exit_dt:
                position_rows.append(row)

        expected_rows = _ordered_expected_rows(position_rows, entry_price, sell_tick_offset)
        min_by_entry = min(expected_rows, key=lambda item: item[3]) if expected_rows else None
        min_by_current = min(expected_rows, key=lambda item: item[4]) if expected_rows else None
        first_below = _first_below_current_threshold(expected_rows, trigger_threshold_percent)
        first_below_entry = next((item for item in expected_rows if item[3] < 0), None)

        intervals = []
        sorted_rows = sorted(position_rows, key=lambda item: _parse_time(item.created_at))
        for prev_row, next_row in zip(sorted_rows, sorted_rows[1:]):
            try:
                intervals.append((_parse_time(next_row.created_at) - _parse_time(prev_row.created_at)).total_seconds())
            except ValueError:
                continue

        first_below_current_price = first_below[1] if first_below else 0
        approx_trigger_pnl = (
            (first_below_current_price - entry_price) / entry_price * 100
            if first_below_current_price > 0 and entry_price > 0
            else None
        )
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
                "min_expected_sell_price": min_by_entry[2] if min_by_entry else "",
                "min_expected_return_vs_entry_percent": round(min_by_entry[3], 4) if min_by_entry else "",
                "first_negative_expect_time": first_below_entry[0].created_at if first_below_entry else "",
                "first_negative_expected_sell_price": first_below_entry[2] if first_below_entry else "",
                "first_negative_expected_return_vs_entry_percent": round(first_below_entry[3], 4) if first_below_entry else "",
                "min_expected_return_vs_current_percent": round(min_by_current[4], 4) if min_by_current else "",
                "dynamic_expect_stop_threshold_percent": trigger_threshold_percent,
                "first_below_threshold_time": first_below[0].created_at if first_below else "",
                "first_below_threshold_current_price": first_below[1] if first_below else "",
                "first_below_threshold_expect_price": first_below[0].expect_price if first_below else "",
                "first_below_threshold_expected_sell_price": first_below[2] if first_below else "",
                "first_below_threshold_expected_return_vs_current_percent": round(first_below[4], 4) if first_below else "",
                "approx_exit_pnl_percent_at_current_price": round(approx_trigger_pnl, 4) if approx_trigger_pnl is not None else "",
            }
        )

        for threshold in sweep_thresholds:
            trigger = _first_below_current_threshold(expected_rows, threshold)
            trigger_current_price = trigger[1] if trigger else 0
            approx_exit_pnl = (
                (trigger_current_price - entry_price) / entry_price * 100
                if trigger_current_price > 0 and entry_price > 0
                else None
            )
            sweep_rows.append(
                {
                    "threshold_percent": threshold,
                    "session_date": session_date,
                    "ticker": ticker,
                    "entry_time": entry_time,
                    "original_exit_time": exit_time,
                    "original_exit_reason": trade.get("exit_reason", ""),
                    "original_pnl_percent": round(original_pnl, 4),
                    "triggered": 1 if trigger else 0,
                    "trigger_time": trigger[0].created_at if trigger else "",
                    "trigger_current_price": trigger[1] if trigger else "",
                    "trigger_expect_price": trigger[0].expect_price if trigger else "",
                    "trigger_expected_sell_price": trigger[2] if trigger else "",
                    "trigger_expected_return_vs_current_percent": round(trigger[4], 4) if trigger else "",
                    "approx_exit_pnl_percent_at_current_price": round(approx_exit_pnl, 4) if approx_exit_pnl is not None else "",
                    "approx_pnl_change_vs_original_percent_point": (
                        round(approx_exit_pnl - original_pnl, 4) if approx_exit_pnl is not None else ""
                    ),
                }
            )

    diagnostic_fields = list(diagnostics[0].keys()) if diagnostics else [
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
        "min_expected_return_vs_current_percent",
        "dynamic_expect_stop_threshold_percent",
        "first_below_threshold_time",
        "first_below_threshold_current_price",
        "first_below_threshold_expect_price",
        "first_below_threshold_expected_sell_price",
        "first_below_threshold_expected_return_vs_current_percent",
        "approx_exit_pnl_percent_at_current_price",
    ]
    sweep_fields = list(sweep_rows[0].keys()) if sweep_rows else [
        "threshold_percent",
        "session_date",
        "ticker",
        "entry_time",
        "original_exit_time",
        "original_exit_reason",
        "original_pnl_percent",
        "triggered",
        "trigger_time",
        "trigger_current_price",
        "trigger_expect_price",
        "trigger_expected_sell_price",
        "trigger_expected_return_vs_current_percent",
        "approx_exit_pnl_percent_at_current_price",
        "approx_pnl_change_vs_original_percent_point",
    ]
    _write_rows(out_path, diagnostics, diagnostic_fields)
    _write_rows(sweep_out_path, sweep_rows, sweep_fields)
    return diagnostics, sweep_rows


def _print_sweep_summary(sweep_rows: list[dict[str, object]]) -> None:
    print("threshold_sweep:")
    thresholds = sorted({float(row["threshold_percent"]) for row in sweep_rows}, reverse=True)
    for threshold in thresholds:
        rows = [row for row in sweep_rows if float(row["threshold_percent"]) == threshold]
        triggered = [row for row in rows if int(row["triggered"] or 0) == 1]
        caught_losses = [row for row in triggered if float(row["original_pnl_percent"] or 0) < 0]
        cut_winners = [row for row in triggered if float(row["original_pnl_percent"] or 0) > 0]
        improved = [
            row
            for row in triggered
            if row["approx_pnl_change_vs_original_percent_point"] != ""
            and float(row["approx_pnl_change_vs_original_percent_point"]) > 0
        ]
        approx_pnls = [
            float(row["approx_exit_pnl_percent_at_current_price"])
            for row in triggered
            if row["approx_exit_pnl_percent_at_current_price"] != ""
        ]
        avg_approx_pnl = sum(approx_pnls) / len(approx_pnls) if approx_pnls else 0.0
        print(
            f"  threshold={threshold:.2f}% triggered={len(triggered)}/{len(rows)} "
            f"caught_original_losses={len(caught_losses)} cut_original_winners={len(cut_winners)} "
            f"improved_vs_original={len(improved)} avg_approx_trigger_pnl={avg_approx_pnl:.4f}%"
        )


def main() -> None:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
    config_args, remaining = config_parser.parse_known_args()
    cfg = replay._load_backtest_default_config(config_args.config)
    strategy_cfg = cfg.get("strategy", {})

    parser = argparse.ArgumentParser(
        description="Diagnose held-position expected returns and dynamic expected-return stop thresholds.",
        parents=[config_parser],
    )
    parser.add_argument("--db", default="Daily_bot/bot.sqlite3")
    parser.add_argument("--trades", default="", help="Replay trade CSV. If omitted, common output paths are searched automatically.")
    parser.add_argument("--out", default="Daily_bot/backtest/results/dynamic_expect_stop_diagnostics.csv")
    parser.add_argument(
        "--sweep-out",
        default="Daily_bot/backtest/results/dynamic_expect_stop_threshold_sweep.csv",
    )
    parser.add_argument("--trigger-threshold", type=float, default=-0.3)
    parser.add_argument(
        "--sweep-threshold",
        type=float,
        action="append",
        dest="sweep_thresholds",
        default=None,
        help="Expected-return threshold to test. Repeat for multiple values. Defaults to -0.1 through -0.5.",
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
    args = parser.parse_args(remaining)
    trades_path = _resolve_trades_path(args.trades)
    sweep_thresholds = list(args.sweep_thresholds or DEFAULT_SWEEP_THRESHOLDS)
    print(f"using_trades={trades_path}")

    diagnostics, sweep_rows = diagnose(
        db_path=Path(args.db),
        trades_path=trades_path,
        out_path=Path(args.out),
        sweep_out_path=Path(args.sweep_out),
        sell_tick_offset=args.sell_tick_offset,
        orderbook_levels_per_side=args.orderbook_levels_per_side,
        bid_decay_min_weight=args.orderbook_bid_linear_decay_min_weight,
        ask_decay_min_weight=args.orderbook_ask_linear_decay_min_weight,
        trigger_threshold_percent=args.trigger_threshold,
        sweep_thresholds=sweep_thresholds,
    )

    threshold_trigger_count = sum(1 for row in diagnostics if row["first_below_threshold_time"])
    negative_entry_count = sum(1 for row in diagnostics if row["first_negative_expect_time"])
    zero_refresh_count = sum(1 for row in diagnostics if int(row["refresh_count_after_entry"] or 0) == 0)
    print(f"diagnosed_trades={len(diagnostics)}")
    print(f"trades_below_{args.trigger_threshold:.2f}pct_current_expectation={threshold_trigger_count}")
    print(f"trades_with_expected_sell_below_entry={negative_entry_count}")
    print(f"trades_with_zero_refreshes={zero_refresh_count}")
    print(f"wrote {args.out}")
    print(f"wrote {args.sweep_out}")
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
            f"min_expected_vs_current={row['min_expected_return_vs_current_percent']}% "
            f"first_below_threshold={row['first_below_threshold_time'] or '-'} "
            f"approx_trigger_pnl={row['approx_exit_pnl_percent_at_current_price'] if row['approx_exit_pnl_percent_at_current_price'] != '' else '-'}%"
        )
    _print_sweep_summary(sweep_rows)


if __name__ == "__main__":
    main()
