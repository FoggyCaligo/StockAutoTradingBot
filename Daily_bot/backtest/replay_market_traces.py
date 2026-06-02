from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean


@dataclass
class TraceRow:
    session_date: str
    ticker: str
    created_at: str
    phase: str
    selected: int
    price: int
    current_price: int
    expect_price: int
    expect_revenue_percent: float
    spread_percent: float


@dataclass
class BacktestTrade:
    session_date: str
    ticker: str
    entry_time: str
    exit_time: str
    entry_price: int
    exit_price: int
    exit_reason: str
    pnl_percent: float


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def load_traces(db_path: Path) -> list[TraceRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            session_date,
            ticker,
            created_at,
            phase,
            selected,
            price,
            current_price,
            expect_price,
            expect_revenue_percent,
            spread_percent
        FROM market_traces
        ORDER BY session_date, created_at, ticker
        """
    ).fetchall()
    conn.close()
    return [
        TraceRow(
            session_date=row["session_date"],
            ticker=row["ticker"],
            created_at=row["created_at"],
            phase=row["phase"],
            selected=_to_int(row["selected"]),
            price=_to_int(row["price"]),
            current_price=_to_int(row["current_price"]),
            expect_price=_to_int(row["expect_price"]),
            expect_revenue_percent=_to_float(row["expect_revenue_percent"]),
            spread_percent=_to_float(row["spread_percent"]),
        )
        for row in rows
    ]


def group_by_session_and_ticker(rows: list[TraceRow]) -> dict[tuple[str, str], list[TraceRow]]:
    grouped: dict[tuple[str, str], list[TraceRow]] = {}
    for row in rows:
        grouped.setdefault((row.session_date, row.ticker), []).append(row)
    return grouped


def pick_entries(
    grouped: dict[tuple[str, str], list[TraceRow]],
    min_expected_return_percent: float,
    max_spread_percent: float,
    top_n_per_day: int,
) -> dict[str, list[TraceRow]]:
    first_rows: list[TraceRow] = []
    for trace_rows in grouped.values():
        first = trace_rows[0]
        if first.current_price <= 0:
            continue
        if first.expect_revenue_percent < min_expected_return_percent:
            continue
        if max_spread_percent > 0 and first.spread_percent > max_spread_percent:
            continue
        first_rows.append(first)

    per_day: dict[str, list[TraceRow]] = {}
    for row in sorted(first_rows, key=lambda item: (item.session_date, -item.expect_revenue_percent)):
        day_entries = per_day.setdefault(row.session_date, [])
        if len(day_entries) < top_n_per_day:
            day_entries.append(row)
    return per_day


def replay_trade(
    entry: TraceRow,
    trace_rows: list[TraceRow],
    take_profit_percent: float,
    stop_loss_percent: float,
) -> BacktestTrade:
    entry_price = entry.current_price or entry.price
    if entry_price <= 0:
        raise ValueError(f"Invalid entry price for {entry.session_date} {entry.ticker}")

    take_profit_price = entry_price * (1 + take_profit_percent / 100)
    stop_loss_price = entry_price * (1 - stop_loss_percent / 100)

    exit_row = trace_rows[-1]
    exit_reason = "force_exit_last_trace"
    for row in trace_rows:
        current = row.current_price
        if current <= 0:
            continue
        if current >= take_profit_price:
            exit_row = row
            exit_reason = "take_profit"
            break
        if current <= stop_loss_price:
            exit_row = row
            exit_reason = "stop_loss"
            break

    exit_price = exit_row.current_price or exit_row.price
    pnl_percent = (exit_price - entry_price) / entry_price * 100
    return BacktestTrade(
        session_date=entry.session_date,
        ticker=entry.ticker,
        entry_time=entry.created_at,
        exit_time=exit_row.created_at,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_percent=pnl_percent,
    )


def run_backtest(
    db_path: Path,
    min_expected_return_percent: float,
    max_spread_percent: float,
    top_n_per_day: int,
    take_profit_percent: float,
    stop_loss_percent: float,
) -> list[BacktestTrade]:
    traces = load_traces(db_path)
    grouped = group_by_session_and_ticker(traces)
    entries_by_day = pick_entries(grouped, min_expected_return_percent, max_spread_percent, top_n_per_day)
    trades: list[BacktestTrade] = []
    for entries in entries_by_day.values():
        for entry in entries:
            trace_rows = grouped[(entry.session_date, entry.ticker)]
            trades.append(replay_trade(entry, trace_rows, take_profit_percent, stop_loss_percent))
    return trades


def write_csv(path: Path, trades: list[BacktestTrade]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "session_date",
                "ticker",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "exit_reason",
                "pnl_percent",
            ],
        )
        writer.writeheader()
        for trade in trades:
            writer.writerow({**trade.__dict__, "pnl_percent": round(trade.pnl_percent, 4)})


def print_summary(trades: list[BacktestTrade]) -> None:
    if not trades:
        print("No trades replayed. Check market_traces data and filters.")
        return
    wins = [trade for trade in trades if trade.pnl_percent > 0]
    losses = [trade for trade in trades if trade.pnl_percent <= 0]
    avg = mean(trade.pnl_percent for trade in trades)
    total = sum(trade.pnl_percent for trade in trades)
    print(f"trades={len(trades)} wins={len(wins)} losses={len(losses)} win_rate={len(wins) / len(trades) * 100:.2f}%")
    print(f"avg_pnl={avg:.4f}% summed_pnl={total:.4f}%")
    print("exit_reasons:")
    for reason in sorted({trade.exit_reason for trade in trades}):
        count = sum(1 for trade in trades if trade.exit_reason == reason)
        print(f"  {reason}: {count}")


def parse_args():
    parser = argparse.ArgumentParser(description="Replay Daily_bot market_traces from bot.sqlite3.")
    parser.add_argument("--db", default="bot.sqlite3")
    parser.add_argument("--min-expected-return", type=float, default=0.25)
    parser.add_argument("--max-spread", type=float, default=0.7)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--take-profit", type=float, default=0.25)
    parser.add_argument("--stop-loss", type=float, default=6.0)
    parser.add_argument("--out", default="Daily_bot/logs/backtest_replay.csv")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = run_backtest(
        db_path=Path(args.db),
        min_expected_return_percent=args.min_expected_return,
        max_spread_percent=args.max_spread,
        top_n_per_day=args.top_n,
        take_profit_percent=args.take_profit,
        stop_loss_percent=args.stop_loss,
    )
    write_csv(Path(args.out), result)
    print_summary(result)
    print(f"wrote {args.out}")
