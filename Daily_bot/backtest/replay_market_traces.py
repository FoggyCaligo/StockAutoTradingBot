from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot.backtest.replay_db_builder import resolve_replay_db_path
from Daily_bot.models import Candidate, Fill
from Daily_bot.risk.guards import calc_order_quantity, passes_orderbook_ask_depth_ratio
from Daily_bot.storage.audit_csv import (
    DEFAULT_FEE_RATE,
    DEFAULT_SELL_TAX_RATE,
    rewrite_fill_audit_csv,
)
from Daily_bot.storage.db import DAILY_REV_FIELDNAMES
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price
from Daily_bot.strategy.signal import min_expected_return_with_spread
from Daily_bot.utils import ceil_tick_count, count_ticks_between_prices, get_tick_size, move_price_by_ticks, round_to_tick


@dataclass
class TraceRow:
    session_date: str
    ticker: str
    created_at: str
    phase: str
    selected: int
    price: int
    prev_close_price: int
    current_price: int
    expect_price: int
    expect_revenue_percent: float
    spread_percent: float
    ask_depth_5_amount_krw: int
    prev_day_change_percent: float


@dataclass
class SelectedSignal:
    session_date: str
    ticker: str
    created_at: str
    price: int
    expect_price: int
    expect_revenue_percent: float
    spread_percent: float


@dataclass
class BacktestTrade:
    session_date: str
    ticker: str
    entry_time: str
    exit_time: str
    quantity: int
    entry_price: int
    exit_price: int
    buy_amount_krw: int
    sell_amount_krw: int
    exit_reason: str
    pnl_percent: float


@dataclass
class ReplayPosition:
    entry: TraceRow
    quantity: int
    invested_amount: int
    entry_price: int
    target_price: int
    stop_loss_price: float


@dataclass
class BacktestSummary:
    trades: int
    wins: int
    losses: int
    win_rate_percent: float
    avg_pnl_percent: float
    total_pnl_percent: float


@dataclass
class BacktestCoverage:
    eligible_candidates: int
    candidates_with_ask_depth: int
    candidates_missing_ask_depth: int
    blocked_by_ask_depth_ratio: int
    skipped_due_to_missing_ask_depth: int


@dataclass
class SessionCapitalPlan:
    session_capital_basis: int
    slot_count: int
    slot_budget_per_stock: int
    position_limit: int


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


def _normalize_created_at(session_date: str, created_at: str) -> str:
    if not created_at:
        return created_at
    try:
        dt = datetime.fromisoformat(created_at)
    except ValueError:
        return created_at
    if dt.hour < 6:
        dt += timedelta(hours=9)
    normalized = dt.strftime("%Y-%m-%d %H:%M:%S")
    if session_date and normalized[:10] != session_date:
        return created_at
    return normalized


def load_traces(db_path: Path) -> list[TraceRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(market_traces)").fetchall()
    }
    ask_depth_select = "ask_depth_5_amount_krw" if "ask_depth_5_amount_krw" in columns else "0 AS ask_depth_5_amount_krw"
    prev_close_select = "prev_close_price" if "prev_close_price" in columns else "0 AS prev_close_price"
    prev_day_change_select = "prev_day_change_percent" if "prev_day_change_percent" in columns else "0 AS prev_day_change_percent"
    rows = conn.execute(
        f"""
        SELECT
            session_date,
            ticker,
            created_at,
            phase,
            selected,
            price,
            {prev_close_select},
            current_price,
            expect_price,
            expect_revenue_percent,
            spread_percent,
            {ask_depth_select},
            {prev_day_change_select}
        FROM market_traces
        ORDER BY session_date, created_at, ticker
        """
    ).fetchall()
    conn.close()
    traces: list[TraceRow] = []
    for row in rows:
        session_date = row["session_date"]
        traces.append(
            TraceRow(
                session_date=session_date,
                ticker=row["ticker"],
                created_at=_normalize_created_at(session_date, row["created_at"]),
                phase=row["phase"],
                selected=_to_int(row["selected"]),
                price=_to_int(row["price"]),
                prev_close_price=_to_int(row["prev_close_price"]),
                current_price=_to_int(row["current_price"]),
                expect_price=_to_int(row["expect_price"]),
                expect_revenue_percent=_to_float(row["expect_revenue_percent"]),
                spread_percent=_to_float(row["spread_percent"]),
                ask_depth_5_amount_krw=_to_int(row["ask_depth_5_amount_krw"]),
                prev_day_change_percent=_to_float(row["prev_day_change_percent"]),
            )
        )
    return traces


def _resolve_prev_day_change_percent(row: TraceRow) -> float:
    if row.prev_close_price > 0 and row.current_price > 0:
        return ((row.current_price - row.prev_close_price) / row.prev_close_price) * 100
    return row.prev_day_change_percent


def load_selected_signals(db_path: Path) -> list[SelectedSignal]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            ticker,
            created_at,
            price,
            expect_price,
            expect_revenue_percent,
            spread_percent
        FROM signals
        WHERE selected = 1
        ORDER BY created_at, ticker
        """
    ).fetchall()
    conn.close()
    signals: list[SelectedSignal] = []
    for row in rows:
        normalized_created_at = _normalize_created_at("", row["created_at"])
        session_date = normalized_created_at[:10] if normalized_created_at else str(row["created_at"] or "")[:10]
        signals.append(
            SelectedSignal(
                session_date=session_date,
                ticker=row["ticker"],
                created_at=normalized_created_at,
                price=_to_int(row["price"]),
                expect_price=_to_int(row["expect_price"]),
                expect_revenue_percent=_to_float(row["expect_revenue_percent"]),
                spread_percent=_to_float(row["spread_percent"]),
            )
        )
    return signals


def load_session_capital_bases(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT session_date, cash, created_at
            FROM account_traces
            ORDER BY session_date, created_at
            """
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {}
    conn.close()
    bases: dict[str, int] = {}
    for row in rows:
        session_date = str(row["session_date"] or "")
        if session_date in bases:
            continue
        cash = _to_int(row["cash"])
        if cash > 0:
            bases[session_date] = cash
    return bases


def group_by_session_and_ticker(rows: list[TraceRow]) -> dict[tuple[str, str], list[TraceRow]]:
    grouped: dict[tuple[str, str], list[TraceRow]] = {}
    for row in rows:
        grouped.setdefault((row.session_date, row.ticker), []).append(row)
    return grouped


def group_by_session(rows: list[TraceRow]) -> dict[str, list[TraceRow]]:
    grouped: dict[str, list[TraceRow]] = {}
    for row in rows:
        grouped.setdefault(row.session_date, []).append(row)
    return grouped


def resolve_total_slot_count(
    total_capital: int,
    min_slot_count: int,
    max_slot_count: int,
    slot_budget_unit_krw: int,
    max_budget_per_stock_krw: int,
    target_budget_ratio_per_stock: float = 0.0,
) -> int:
    if total_capital <= 0:
        return 0
    min_slots = max(1, int(min_slot_count or 1))
    if slot_budget_unit_krw > 0:
        slot_count = max(min_slots, total_capital // slot_budget_unit_krw)
        return min(slot_count, max_slot_count) if max_slot_count > 0 else slot_count
    if max_budget_per_stock_krw > 0:
        affordable = max(1, total_capital // max_budget_per_stock_krw)
    elif target_budget_ratio_per_stock > 0:
        budget = int(total_capital * target_budget_ratio_per_stock)
        affordable = max(1, total_capital // budget) if budget > 0 else min_slots
    else:
        affordable = min_slots
    slot_count = max(min_slots, affordable)
    return min(slot_count, max_slot_count) if max_slot_count > 0 else slot_count


def resolve_target_budget_per_stock(
    planning_cash: int,
    slot_count: int,
    max_budget_per_stock_krw: int,
    slot_budget_unit_krw: int,
    max_slot_count: int,
    target_budget_ratio_per_stock: float = 0.0,
) -> int:
    if planning_cash <= 0 or slot_count <= 0:
        return 0
    if slot_budget_unit_krw > 0:
        raw_slot_count = max(1, planning_cash // slot_budget_unit_krw)
        budget_from_slots = planning_cash // slot_count
        if max_slot_count > 0 and raw_slot_count > max_slot_count:
            return min(budget_from_slots, max_budget_per_stock_krw) if max_budget_per_stock_krw > 0 else budget_from_slots
        if max_budget_per_stock_krw > 0:
            return min(budget_from_slots, max_budget_per_stock_krw)
        return budget_from_slots
    if target_budget_ratio_per_stock > 0:
        budget_from_ratio = int(planning_cash * target_budget_ratio_per_stock)
        if max_budget_per_stock_krw > 0 and budget_from_ratio > 0:
            return min(budget_from_ratio, max_budget_per_stock_krw)
        if budget_from_ratio > 0:
            return budget_from_ratio
    return max_budget_per_stock_krw


def resolve_position_limit(slot_count: int, max_position_count: int) -> int:
    hard_limit = int(max_position_count or 0)
    if hard_limit > 0 and slot_count > 0:
        return min(hard_limit, slot_count)
    if hard_limit > 0:
        return hard_limit
    return slot_count


def resolve_session_capital_plan(
    session_date: str,
    session_capital_by_day: dict[str, int] | None,
    default_starting_capital_krw: int,
    min_slot_count: int,
    max_slot_count: int,
    slot_budget_unit_krw: int,
    max_budget_per_stock_krw: int,
    max_position_count: int,
    target_budget_ratio_per_stock: float = 0.0,
) -> SessionCapitalPlan:
    session_capital_basis = max(0, int((session_capital_by_day or {}).get(session_date, default_starting_capital_krw) or 0))
    slot_count = resolve_total_slot_count(
        total_capital=session_capital_basis,
        min_slot_count=min_slot_count,
        max_slot_count=max_slot_count,
        slot_budget_unit_krw=slot_budget_unit_krw,
        max_budget_per_stock_krw=max_budget_per_stock_krw,
        target_budget_ratio_per_stock=target_budget_ratio_per_stock,
    )
    slot_budget_per_stock = resolve_target_budget_per_stock(
        planning_cash=session_capital_basis,
        slot_count=slot_count,
        max_budget_per_stock_krw=max_budget_per_stock_krw,
        slot_budget_unit_krw=slot_budget_unit_krw,
        max_slot_count=max_slot_count,
        target_budget_ratio_per_stock=target_budget_ratio_per_stock,
    )
    position_limit = resolve_position_limit(slot_count=slot_count, max_position_count=max_position_count)
    return SessionCapitalPlan(
        session_capital_basis=session_capital_basis,
        slot_count=slot_count,
        slot_budget_per_stock=slot_budget_per_stock,
        position_limit=position_limit,
    )


def _min_sell_price_above_buy(buy_price: int) -> int:
    if buy_price <= 0:
        return 0
    return round_to_tick(buy_price + get_tick_size(buy_price))


def _resolve_target_price(expect_price: int, sell_tick_offset: int, entry_price: int) -> int:
    target_price = calc_target_sell_price(expect_price, sell_tick_offset) if expect_price > 0 else 0
    min_sell_price = _min_sell_price_above_buy(entry_price)
    if min_sell_price > 0 and target_price < min_sell_price:
        return min_sell_price
    return target_price if target_price > 0 else min_sell_price


def _resolve_stop_loss_price(
    entry_price: int,
    expect_price: int,
    stop_loss_percent: float,
    stop_loss_tick_count: int,
    stop_loss_tick_multiplier: float,
) -> float:
    upward_ticks = count_ticks_between_prices(entry_price, expect_price)
    dynamic_tick_distance = ceil_tick_count(upward_ticks * max(stop_loss_tick_multiplier, 0.0))
    if dynamic_tick_distance <= 0 and stop_loss_tick_multiplier > 0:
        dynamic_tick_distance = 1
    stop_tick_distance = max(stop_loss_tick_count, dynamic_tick_distance)
    if stop_tick_distance > 0:
        return float(move_price_by_ticks(entry_price, -stop_tick_distance))
    if stop_loss_percent > 0:
        return entry_price * (1 - stop_loss_percent / 100)
    return 0.0


def _is_within_buy_window(created_at: str, start_buy_time: str, stop_buy_time: str) -> bool:
    return _is_within_time_window(created_at, start_buy_time, stop_buy_time)


def _extract_time_text(created_at: str) -> str:
    return created_at[11:16] if len(created_at) >= 16 else ""


def _is_within_time_window(created_at: str, start_time: str, end_time: str) -> bool:
    time_text = _extract_time_text(created_at)
    return bool(time_text) and start_time <= time_text <= end_time


def _is_after_time(created_at: str, target_time: str) -> bool:
    time_text = _extract_time_text(created_at)
    return bool(time_text) and time_text >= target_time


def _parse_timestamp(timestamp_text: str) -> datetime:
    normalized = str(timestamp_text or "").strip().replace(" ", "T")
    return datetime.fromisoformat(normalized)


def _make_order_id(prefix: str, trade: BacktestTrade) -> str:
    timestamp = str(trade.entry_time if prefix == "BUY" else trade.exit_time).replace(" ", "T").replace(":", "").replace("-", "")
    return f"{prefix}-{trade.session_date}-{trade.ticker}-{timestamp}"


def _derived_output_path(base_path: Path, suffix: str) -> Path:
    return base_path.with_name(f"{base_path.stem}_{suffix}{base_path.suffix}")


def _selected_tickers_by_day(selected_signals: list[SelectedSignal]) -> dict[str, set[str]]:
    by_day: dict[str, set[str]] = {}
    for signal in selected_signals:
        by_day.setdefault(signal.session_date, set()).add(signal.ticker)
    return by_day


def _pick_candidates_for_timestamp(
    rows: list[TraceRow],
    min_expected_return_percent: float,
    max_spread_percent: float,
    top_ratio: float,
    spread_expected_return_multiplier: float,
    min_prev_day_change_percent: float,
    max_prev_day_change_percent: float,
    active_tickers: set[str],
    allowed_tickers: set[str] | None,
) -> list[TraceRow]:
    latest_by_ticker: dict[str, TraceRow] = {}
    for row in rows:
        if row.phase not in {"scan_candidate", "watchlist"}:
            continue
        if row.ticker in active_tickers:
            continue
        if allowed_tickers is not None and row.ticker not in allowed_tickers:
            continue
        if row.current_price <= 0:
            continue
        if min_prev_day_change_percent < 0 and _resolve_prev_day_change_percent(row) > min_prev_day_change_percent:
            continue
        if max_prev_day_change_percent > 0 and _resolve_prev_day_change_percent(row) >= max_prev_day_change_percent:
            continue
        if max_spread_percent > 0 and row.spread_percent > max_spread_percent:
            continue
        required_expected_return = min_expected_return_with_spread(
            min_expected_return_percent=min_expected_return_percent,
            spread_percent=row.spread_percent,
            spread_expected_return_multiplier=spread_expected_return_multiplier,
        )
        if row.expect_revenue_percent < required_expected_return:
            continue
        existing = latest_by_ticker.get(row.ticker)
        if existing is None or row.expect_revenue_percent > existing.expect_revenue_percent:
            latest_by_ticker[row.ticker] = row

    candidates = sorted(
        latest_by_ticker.values(),
        key=lambda item: (-item.expect_revenue_percent, item.ticker),
    )
    if not candidates:
        return []
    if 0 < top_ratio < 1:
        keep_count = max(1, math.ceil(len(candidates) * top_ratio))
        return candidates[:keep_count]
    return candidates


def summarize_ask_depth_coverage(
    trades: list[BacktestTrade],
    traces: list[TraceRow],
    max_orderbook_ask_depth_ratio: float = 0.0,
    missing_ask_depth_policy: str = "ignore",
) -> BacktestCoverage:
    if not traces:
        return BacktestCoverage(0, 0, 0, 0, 0)
    if max_orderbook_ask_depth_ratio <= 0:
        return BacktestCoverage(0, 0, 0, 0, 0)

    traded_keys = {(trade.session_date, trade.ticker, trade.entry_time) for trade in trades}
    eligible_candidates = 0
    with_ask_depth = 0
    missing_ask_depth = 0
    blocked_by_ratio = 0
    skipped_missing = 0

    for row in traces:
        if row.phase not in {"scan_candidate", "watchlist"}:
            continue
        if (row.session_date, row.ticker, row.created_at) not in traded_keys:
            continue
        eligible_candidates += 1
        if row.ask_depth_5_amount_krw > 0:
            with_ask_depth += 1
            candidate = Candidate(
                ticker=row.ticker,
                price=row.current_price or row.price,
                expect_price=row.expect_price,
                expect_revenue_percent=row.expect_revenue_percent,
                spread_percent=row.spread_percent,
                ask_depth_5_amount_krw=row.ask_depth_5_amount_krw,
            )
            quantity = 1
            estimated_cost = candidate.price * quantity
            if candidate.price > 0 and not passes_orderbook_ask_depth_ratio(
                candidate,
                estimated_cost_krw=estimated_cost,
                max_orderbook_ask_depth_ratio=max_orderbook_ask_depth_ratio,
            ):
                blocked_by_ratio += 1
        else:
            missing_ask_depth += 1
            if missing_ask_depth_policy == "skip":
                skipped_missing += 1

    return BacktestCoverage(
        eligible_candidates=eligible_candidates,
        candidates_with_ask_depth=with_ask_depth,
        candidates_missing_ask_depth=missing_ask_depth,
        blocked_by_ask_depth_ratio=blocked_by_ratio,
        skipped_due_to_missing_ask_depth=skipped_missing,
    )


def pick_entries(
    grouped: dict[tuple[str, str], list[TraceRow]],
    min_expected_return_percent: float,
    max_spread_percent: float,
    top_n_per_day: int,
    min_prev_day_change_percent: float = 0.0,
    max_prev_day_change_percent: float = 0.0,
    spread_expected_return_multiplier: float = 0.0,
    selected_signals: list[SelectedSignal] | None = None,
) -> dict[str, list[TraceRow]]:
    if selected_signals:
        per_day: dict[str, list[TraceRow]] = {}
        for signal in selected_signals:
            trace_rows = grouped.get((signal.session_date, signal.ticker))
            if not trace_rows:
                continue
            day_entries = per_day.setdefault(signal.session_date, [])
            if len(day_entries) < top_n_per_day:
                day_entries.append(trace_rows[0])
        if per_day:
            return per_day

    first_rows: list[TraceRow] = []
    for trace_rows in grouped.values():
        first = trace_rows[0]
        if first.current_price <= 0:
            continue
        if min_prev_day_change_percent < 0 and _resolve_prev_day_change_percent(first) > min_prev_day_change_percent:
            continue
        if max_prev_day_change_percent > 0 and _resolve_prev_day_change_percent(first) >= max_prev_day_change_percent:
            continue
        if max_spread_percent > 0 and first.spread_percent > max_spread_percent:
            continue
        required_expected_return = min_expected_return_with_spread(
            min_expected_return_percent=min_expected_return_percent,
            spread_percent=first.spread_percent,
            spread_expected_return_multiplier=spread_expected_return_multiplier,
        )
        if first.expect_revenue_percent < required_expected_return:
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
        quantity=1,
        entry_price=entry_price,
        exit_price=exit_price,
        buy_amount_krw=entry_price,
        sell_amount_krw=exit_price,
        exit_reason=exit_reason,
        pnl_percent=pnl_percent,
    )


def run_backtest(
    db_path: Path,
    min_expected_return_percent: float,
    max_spread_percent: float,
    top_n_per_day: int,
    stop_loss_percent: float,
    min_prev_day_change_percent: float = 0.0,
    max_prev_day_change_percent: float = 0.0,
    stop_loss_tick_count: int = 0,
    stop_loss_tick_multiplier: float = 2.0,
    use_selected_signals: bool = True,
    take_profit_percent: float = 0.25,
    top_ratio: float = 1.0,
    sell_tick_offset: int = 1,
    session_capital_by_day: dict[str, int] | None = None,
    default_starting_capital_krw: int = 0,
    min_slot_count: int = 1,
    max_slot_count: int = 0,
    slot_budget_unit_krw: int = 0,
    max_budget_per_stock_krw: int = 0,
    max_position_count: int = 0,
    target_budget_ratio_per_stock: float = 0.0,
    start_buy_time: str = "09:30",
    stop_buy_time: str = "13:00",
    force_sell_time: str = "15:00",
    spread_expected_return_multiplier: float = 0.0,
    max_orderbook_ask_depth_ratio: float = 0.0,
    missing_ask_depth_policy: str = "ignore",
) -> list[BacktestTrade]:
    traces = load_traces(db_path)
    selected_signals = load_selected_signals(db_path) if use_selected_signals else []
    selected_tickers = _selected_tickers_by_day(selected_signals)
    grouped_by_session = group_by_session(traces)
    trades: list[BacktestTrade] = []

    for session_date, day_rows in grouped_by_session.items():
        session_plan = resolve_session_capital_plan(
            session_date=session_date,
            session_capital_by_day=session_capital_by_day,
            default_starting_capital_krw=default_starting_capital_krw,
            min_slot_count=min_slot_count,
            max_slot_count=max_slot_count,
            slot_budget_unit_krw=slot_budget_unit_krw,
            max_budget_per_stock_krw=max_budget_per_stock_krw,
            max_position_count=max_position_count,
            target_budget_ratio_per_stock=target_budget_ratio_per_stock,
        )
        if session_plan.position_limit <= 0 or session_plan.slot_budget_per_stock <= 0 or session_plan.session_capital_basis <= 0:
            continue
        rows_by_time: dict[str, list[TraceRow]] = {}
        last_row_by_ticker: dict[str, TraceRow] = {}
        for row in day_rows:
            rows_by_time.setdefault(row.created_at, []).append(row)
            last_row_by_ticker[row.ticker] = row

        open_positions: dict[str, ReplayPosition] = {}
        allowed_tickers = selected_tickers.get(session_date) if use_selected_signals and session_date in selected_tickers else None
        available_cash = session_plan.session_capital_basis

        for created_at in sorted(rows_by_time):
            rows_at_time = rows_by_time[created_at]
            rows_by_ticker = {row.ticker: row for row in rows_at_time}
            exited_tickers_at_time: set[str] = set()

            for ticker, position in list(open_positions.items()):
                current_row = rows_by_ticker.get(ticker)
                if current_row is None:
                    continue
                current_price = current_row.current_price or current_row.price
                if current_price <= 0:
                    continue
                if _is_after_time(current_row.created_at, force_sell_time):
                    trades.append(
                        BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=current_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * current_price,
                            exit_reason="force_exit_time",
                            pnl_percent=(current_price - position.entry_price) / position.entry_price * 100,
                        )
                    )
                    available_cash += position.quantity * current_price
                    del open_positions[ticker]
                    exited_tickers_at_time.add(ticker)
                    continue
                if current_price <= position.stop_loss_price:
                    trades.append(
                        BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=current_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * current_price,
                            exit_reason="stop_loss",
                            pnl_percent=(current_price - position.entry_price) / position.entry_price * 100,
                        )
                    )
                    available_cash += position.quantity * current_price
                    del open_positions[ticker]
                    exited_tickers_at_time.add(ticker)
                    continue
                if current_price >= position.target_price:
                    trades.append(
                        BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=current_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * current_price,
                            exit_reason="take_profit",
                            pnl_percent=(current_price - position.entry_price) / position.entry_price * 100,
                        )
                    )
                    available_cash += position.quantity * current_price
                    del open_positions[ticker]
                    exited_tickers_at_time.add(ticker)

            if not _is_within_buy_window(created_at, start_buy_time, stop_buy_time):
                continue

            # Match the live bot's set-based rebuy flow: once any position is open,
            # wait for the whole set to clear before opening the next batch.
            if open_positions:
                continue

            # Match the live bot's behavior by default: candidate ranking is trimmed by
            # top_ratio, while the actual buy count is bounded by session position limits,
            # empty slots, and cash. top_n_per_day remains as an optional extra cap only
            # when explicitly provided.
            effective_position_limit = (
                min(top_n_per_day, session_plan.position_limit)
                if top_n_per_day > 0
                else session_plan.position_limit
            )
            available_slots = max(0, effective_position_limit - len(open_positions))
            if available_slots <= 0:
                continue
            affordable_slots = available_cash // session_plan.slot_budget_per_stock if session_plan.slot_budget_per_stock > 0 else 0
            planned_buy_count = min(available_slots, affordable_slots)
            if planned_buy_count <= 0:
                continue

            candidates = _pick_candidates_for_timestamp(
                rows_at_time,
                min_expected_return_percent=min_expected_return_percent,
                max_spread_percent=max_spread_percent,
                top_ratio=top_ratio,
                spread_expected_return_multiplier=spread_expected_return_multiplier,
                min_prev_day_change_percent=min_prev_day_change_percent,
                max_prev_day_change_percent=max_prev_day_change_percent,
                active_tickers=set(open_positions) | exited_tickers_at_time,
                allowed_tickers=allowed_tickers,
            )

            for candidate in candidates[:planned_buy_count]:
                entry_price = candidate.current_price or candidate.price
                if entry_price <= 0:
                    continue
                candidate_model = Candidate(
                    ticker=candidate.ticker,
                    price=entry_price,
                    expect_price=candidate.expect_price,
                    expect_revenue_percent=candidate.expect_revenue_percent,
                    spread_percent=candidate.spread_percent,
                    ask_depth_5_amount_krw=candidate.ask_depth_5_amount_krw,
                )
                quantity = calc_order_quantity(candidate_model, session_plan.slot_budget_per_stock)
                estimated_cost = quantity * entry_price
                if quantity <= 0 or estimated_cost <= 0 or estimated_cost > available_cash:
                    continue
                if max_orderbook_ask_depth_ratio > 0:
                    if candidate.ask_depth_5_amount_krw <= 0:
                        if missing_ask_depth_policy == "skip":
                            continue
                    elif not passes_orderbook_ask_depth_ratio(
                        candidate_model,
                        estimated_cost_krw=estimated_cost,
                        max_orderbook_ask_depth_ratio=max_orderbook_ask_depth_ratio,
                    ):
                        continue
                target_price = _resolve_target_price(candidate.expect_price, sell_tick_offset, entry_price)
                if target_price <= 0:
                    target_price = int(entry_price * (1 + take_profit_percent / 100))
                open_positions[candidate.ticker] = ReplayPosition(
                    entry=candidate,
                    quantity=quantity,
                    invested_amount=estimated_cost,
                    entry_price=entry_price,
                    target_price=target_price,
                    stop_loss_price=_resolve_stop_loss_price(
                        entry_price=entry_price,
                        expect_price=candidate.expect_price,
                        stop_loss_percent=stop_loss_percent,
                        stop_loss_tick_count=stop_loss_tick_count,
                        stop_loss_tick_multiplier=stop_loss_tick_multiplier,
                    ),
                )
                available_cash -= estimated_cost

        for ticker, position in open_positions.items():
            exit_row = last_row_by_ticker.get(ticker, position.entry)
            exit_price = exit_row.current_price or exit_row.price or position.entry_price
            trades.append(
                BacktestTrade(
                    session_date=session_date,
                    ticker=ticker,
                    entry_time=position.entry.created_at,
                    exit_time=exit_row.created_at,
                    quantity=position.quantity,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    buy_amount_krw=position.invested_amount,
                    sell_amount_krw=position.quantity * exit_price,
                    exit_reason="force_exit_last_trace",
                    pnl_percent=(exit_price - position.entry_price) / position.entry_price * 100,
                )
            )
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
                "quantity",
                "entry_price",
                "exit_price",
                "buy_amount_krw",
                "sell_amount_krw",
                "exit_reason",
                "pnl_percent",
            ],
        )
        writer.writeheader()
        for trade in trades:
            writer.writerow({**trade.__dict__, "pnl_percent": round(trade.pnl_percent, 4)})


def _session_starting_capital_by_day(
    session_dates: set[str],
    session_capital_by_day: dict[str, int] | None,
    default_starting_capital_krw: int,
    min_slot_count: int,
    max_slot_count: int,
    slot_budget_unit_krw: int,
    max_budget_per_stock_krw: int,
    max_position_count: int,
    target_budget_ratio_per_stock: float,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for session_date in sorted(session_dates):
        plan = resolve_session_capital_plan(
            session_date=session_date,
            session_capital_by_day=session_capital_by_day,
            default_starting_capital_krw=default_starting_capital_krw,
            min_slot_count=min_slot_count,
            max_slot_count=max_slot_count,
            slot_budget_unit_krw=slot_budget_unit_krw,
            max_budget_per_stock_krw=max_budget_per_stock_krw,
            max_position_count=max_position_count,
            target_budget_ratio_per_stock=target_budget_ratio_per_stock,
        )
        result[session_date] = plan.session_capital_basis
    return result


def write_backtest_daily_revenue_csv(
    path: Path,
    trades: list[BacktestTrade],
    session_starting_capital_by_day: dict[str, int],
    fee_rate: float = DEFAULT_FEE_RATE,
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE,
) -> None:
    rows_by_session: dict[str, dict[str, object]] = {}
    for trade in sorted(trades, key=lambda item: (item.session_date, item.exit_time, item.ticker)):
        session_row = rows_by_session.setdefault(
            trade.session_date,
            {
                "session_date": trade.session_date,
                "starting_capital_krw": int(session_starting_capital_by_day.get(trade.session_date, 0) or 0),
                "total_profit_krw": 0.0,
                "total_fee_krw": 0.0,
                "total_tax_krw": 0.0,
                "total_buy_amount_krw": 0,
                "total_sell_amount_krw": 0,
                "traded_tickers": [],
                "_ticker_set": set(),
            },
        )
        buy_fee = trade.buy_amount_krw * fee_rate
        sell_fee = trade.sell_amount_krw * fee_rate
        sell_tax = trade.sell_amount_krw * sell_tax_rate
        gross_pnl = trade.sell_amount_krw - trade.buy_amount_krw
        session_row["total_profit_krw"] = float(session_row["total_profit_krw"]) + gross_pnl - buy_fee - sell_fee - sell_tax
        session_row["total_fee_krw"] = float(session_row["total_fee_krw"]) + buy_fee + sell_fee
        session_row["total_tax_krw"] = float(session_row["total_tax_krw"]) + sell_tax
        session_row["total_buy_amount_krw"] = int(session_row["total_buy_amount_krw"]) + trade.buy_amount_krw
        session_row["total_sell_amount_krw"] = int(session_row["total_sell_amount_krw"]) + trade.sell_amount_krw
        ticker_set = session_row["_ticker_set"]
        if trade.ticker not in ticker_set:
            ticker_set.add(trade.ticker)
            session_row["traded_tickers"].append(trade.ticker)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=DAILY_REV_FIELDNAMES)
        writer.writeheader()
        for session_date in sorted(rows_by_session):
            row = rows_by_session[session_date]
            total_buy_amount = int(row["total_buy_amount_krw"])
            total_profit = int(round(float(row["total_profit_krw"])))
            starting_capital = int(row["starting_capital_krw"])
            writer.writerow(
                {
                    "session_date": session_date,
                    "starting_capital_krw": starting_capital,
                    "total_profit_krw": total_profit,
                    "total_fee_krw": int(round(float(row["total_fee_krw"]))),
                    "total_tax_krw": int(round(float(row["total_tax_krw"]))),
                    "total_buy_amount_krw": total_buy_amount,
                    "total_sell_amount_krw": int(row["total_sell_amount_krw"]),
                    "total_return_percent": f"{(total_profit / total_buy_amount * 100) if total_buy_amount > 0 else 0.0:.4f}",
                    "total_return_percent_on_starting_capital": (
                        f"{(total_profit / starting_capital * 100) if starting_capital > 0 else 0.0:.4f}"
                    ),
                    "traded_tickers": ",".join(row["traded_tickers"]),
                }
            )


def write_backtest_daily_audit_csv(
    path: Path,
    trades: list[BacktestTrade],
) -> None:
    audit_entries: list[tuple[Fill, str, str]] = []
    for trade in sorted(trades, key=lambda item: (item.session_date, item.entry_time, item.exit_time, item.ticker)):
        entry_dt = _parse_timestamp(trade.entry_time)
        exit_dt = _parse_timestamp(trade.exit_time)
        audit_entries.append(
            (
                Fill(
                    order_id=_make_order_id("BUY", trade),
                    ticker=trade.ticker,
                    quantity=trade.quantity,
                    price=trade.entry_price,
                    filled_at=entry_dt,
                    raw={},
                ),
                "BUY",
                "backtest_replay",
            )
        )
        audit_entries.append(
            (
                Fill(
                    order_id=_make_order_id("SELL", trade),
                    ticker=trade.ticker,
                    quantity=trade.quantity,
                    price=trade.exit_price,
                    filled_at=exit_dt,
                    raw={},
                ),
                "SELL",
                "backtest_replay",
            )
        )
    rewrite_fill_audit_csv(path, audit_entries, reset_by_trade_date=True)


def print_summary(trades: list[BacktestTrade]) -> None:
    summary = summarize_trades(trades)
    if summary.trades == 0:
        print("No trades replayed. Check market_traces data and filters.")
        return
    print(
        f"trades={summary.trades} wins={summary.wins} losses={summary.losses} "
        f"win_rate={summary.win_rate_percent:.2f}%"
    )
    print(f"avg_pnl={summary.avg_pnl_percent:.4f}% summed_pnl={summary.total_pnl_percent:.4f}%")
    print("exit_reasons:")
    for reason in sorted({trade.exit_reason for trade in trades}):
        count = sum(1 for trade in trades if trade.exit_reason == reason)
        print(f"  {reason}: {count}")


def print_ask_depth_coverage(coverage: BacktestCoverage, missing_ask_depth_policy: str) -> None:
    if coverage.eligible_candidates <= 0:
        return
    covered_ratio = coverage.candidates_with_ask_depth / coverage.eligible_candidates * 100
    print("ask_depth_coverage:")
    print(
        f"  entries={coverage.eligible_candidates} with_depth={coverage.candidates_with_ask_depth} "
        f"missing_depth={coverage.candidates_missing_ask_depth} coverage={covered_ratio:.2f}%"
    )
    print(
        f"  blocked_by_depth_ratio={coverage.blocked_by_ask_depth_ratio} "
        f"missing_policy={missing_ask_depth_policy} skipped_missing={coverage.skipped_due_to_missing_ask_depth}"
    )


def summarize_trades(trades: list[BacktestTrade]) -> BacktestSummary:
    if not trades:
        return BacktestSummary(
            trades=0,
            wins=0,
            losses=0,
            win_rate_percent=0.0,
            avg_pnl_percent=0.0,
            total_pnl_percent=0.0,
        )

    wins = [trade for trade in trades if trade.pnl_percent > 0]
    losses = [trade for trade in trades if trade.pnl_percent <= 0]
    avg = mean(trade.pnl_percent for trade in trades)
    total = sum(trade.pnl_percent for trade in trades)
    return BacktestSummary(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_percent=len(wins) / len(trades) * 100,
        avg_pnl_percent=avg,
        total_pnl_percent=total,
    )


def write_backtest_reports(
    out_path: Path,
    trades: list[BacktestTrade],
    session_capital_by_day: dict[str, int] | None,
    default_starting_capital_krw: int,
    min_slot_count: int,
    max_slot_count: int,
    slot_budget_unit_krw: int,
    max_budget_per_stock_krw: int,
    max_position_count: int,
    target_budget_ratio_per_stock: float,
) -> dict[str, Path]:
    write_csv(out_path, trades)
    session_starting_capital = _session_starting_capital_by_day(
        session_dates={trade.session_date for trade in trades},
        session_capital_by_day=session_capital_by_day,
        default_starting_capital_krw=default_starting_capital_krw,
        min_slot_count=min_slot_count,
        max_slot_count=max_slot_count,
        slot_budget_unit_krw=slot_budget_unit_krw,
        max_budget_per_stock_krw=max_budget_per_stock_krw,
        max_position_count=max_position_count,
        target_budget_ratio_per_stock=target_budget_ratio_per_stock,
    )
    daily_rev_path = _derived_output_path(out_path, "daily_rev")
    daily_audit_path = _derived_output_path(out_path, "trade_fills_audit_daily")
    write_backtest_daily_revenue_csv(daily_rev_path, trades, session_starting_capital)
    write_backtest_daily_audit_csv(daily_audit_path, trades)
    return {
        "trades": out_path,
        "daily_rev": daily_rev_path,
        "daily_audit": daily_audit_path,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Replay Daily_bot market_traces from bot.sqlite3.")
    parser.add_argument("--db", default="bot.sqlite3")
    parser.add_argument("--logs-dir", default="")
    parser.add_argument("--min-expected-return", type=float, default=0.25)
    parser.add_argument("--max-spread", type=float, default=0.7)
    parser.add_argument("--min-prev-day-change", type=float, default=0.0)
    parser.add_argument("--max-prev-day-change", type=float, default=0.0)
    parser.add_argument("--top-n", type=int, default=0)
    parser.add_argument("--top-ratio", type=float, default=1.0)
    parser.add_argument("--take-profit", type=float, default=0.25)
    parser.add_argument("--stop-loss", type=float, default=6.0)
    parser.add_argument("--stop-loss-tick-count", type=int, default=0)
    parser.add_argument("--stop-loss-tick-multiplier", type=float, default=2.0)
    parser.add_argument("--sell-tick-offset", type=int, default=1)
    parser.add_argument("--start-buy-time", default="09:30")
    parser.add_argument("--stop-buy-time", default="13:00")
    parser.add_argument("--force-sell-time", default="15:00")
    parser.add_argument("--spread-expected-return-multiplier", type=float, default=0.0)
    parser.add_argument("--max-orderbook-ask-depth-ratio", type=float, default=0.0)
    parser.add_argument("--missing-ask-depth-policy", choices=["ignore", "skip"], default="ignore")
    parser.add_argument("--starting-capital-krw", type=int, default=1_000_000)
    parser.add_argument("--min-slot-count", type=int, default=1)
    parser.add_argument("--max-slot-count", type=int, default=0)
    parser.add_argument("--slot-budget-unit-krw", type=int, default=0)
    parser.add_argument("--max-budget-per-stock-krw", type=int, default=0)
    parser.add_argument("--max-position-count", type=int, default=0)
    parser.add_argument("--target-budget-ratio-per-stock", type=float, default=0.0)
    parser.add_argument("--out", default="Daily_bot/backtest/results/backtest_replay.csv")
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
    session_capital_by_day = load_session_capital_bases(resolved_db_path)
    result = run_backtest(
        db_path=resolved_db_path,
        min_expected_return_percent=args.min_expected_return,
        max_spread_percent=args.max_spread,
        min_prev_day_change_percent=args.min_prev_day_change,
        top_n_per_day=args.top_n,
        max_prev_day_change_percent=args.max_prev_day_change,
        stop_loss_percent=args.stop_loss,
        stop_loss_tick_count=args.stop_loss_tick_count,
        stop_loss_tick_multiplier=args.stop_loss_tick_multiplier,
        use_selected_signals=not args.ignore_selected_signals,
        take_profit_percent=args.take_profit,
        top_ratio=args.top_ratio,
        sell_tick_offset=args.sell_tick_offset,
        session_capital_by_day=session_capital_by_day,
        default_starting_capital_krw=args.starting_capital_krw,
        min_slot_count=args.min_slot_count,
        max_slot_count=args.max_slot_count,
        slot_budget_unit_krw=args.slot_budget_unit_krw,
        max_budget_per_stock_krw=args.max_budget_per_stock_krw,
        max_position_count=args.max_position_count,
        target_budget_ratio_per_stock=args.target_budget_ratio_per_stock,
        start_buy_time=args.start_buy_time,
        stop_buy_time=args.stop_buy_time,
        force_sell_time=args.force_sell_time,
        spread_expected_return_multiplier=args.spread_expected_return_multiplier,
        max_orderbook_ask_depth_ratio=args.max_orderbook_ask_depth_ratio,
        missing_ask_depth_policy=args.missing_ask_depth_policy,
    )
    report_paths = write_backtest_reports(
        out_path=Path(args.out),
        trades=result,
        session_capital_by_day=session_capital_by_day,
        default_starting_capital_krw=args.starting_capital_krw,
        min_slot_count=args.min_slot_count,
        max_slot_count=args.max_slot_count,
        slot_budget_unit_krw=args.slot_budget_unit_krw,
        max_budget_per_stock_krw=args.max_budget_per_stock_krw,
        max_position_count=args.max_position_count,
        target_budget_ratio_per_stock=args.target_budget_ratio_per_stock,
    )
    print_summary(result)
    coverage = summarize_ask_depth_coverage(
        result,
        load_traces(resolved_db_path),
        max_orderbook_ask_depth_ratio=args.max_orderbook_ask_depth_ratio,
        missing_ask_depth_policy=args.missing_ask_depth_policy,
    )
    print_ask_depth_coverage(coverage, args.missing_ask_depth_policy)
    print(f"wrote {report_paths['trades']}")
    print(f"wrote {report_paths['daily_rev']}")
    print(f"wrote {report_paths['daily_audit']}")
