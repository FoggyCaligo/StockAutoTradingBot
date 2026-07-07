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
from Daily_bot.risk.guards import calc_order_quantity, passes_orderbook_ask_depth_ratio, select_affordable_targets
from Daily_bot.storage.audit_csv import (
    DEFAULT_FEE_RATE,
    DEFAULT_SELL_TAX_RATE,
    rewrite_fill_audit_csv,
)
from Daily_bot.storage.db import DAILY_REV_FIELDNAMES
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price
from Daily_bot.strategy.signal import min_expected_return_with_spread
from Daily_bot.utils import ceil_tick_count, count_ticks_between_prices, get_tick_size, load_yaml, move_price_by_ticks, round_to_tick


def _ticker_key(ticker: str) -> str:
    return str(ticker or "").strip().upper().removeprefix("A")


def resolve_fallback_expected_return_thresholds(
    strategy_cfg: dict,
    primary_threshold: float | None = None,
) -> list[float]:
    if primary_threshold is None:
        primary_threshold = float(strategy_cfg.get("min_expected_return_percent", 0.0) or 0.0)
    raw_thresholds = strategy_cfg.get("min_expected_return_fallback_percents")
    if raw_thresholds is None:
        raw_thresholds = strategy_cfg.get("min_expected_return_fallback_percent", 0.0)

    if isinstance(raw_thresholds, (list, tuple)):
        values = raw_thresholds
    else:
        values = [raw_thresholds]

    thresholds: list[float] = []
    for value in values:
        try:
            threshold = float(value or 0.0)
        except (TypeError, ValueError):
            continue
        if 0 < threshold < primary_threshold and threshold not in thresholds:
            thresholds.append(threshold)
    return thresholds


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
    actual_exit_override: "ActualExitOverride | None" = None


@dataclass
class ActualExitOverride:
    session_date: str
    ticker: str
    buy_filled_at: str
    entry_price: int
    quantity: int
    final_exit_time: str
    weighted_exit_price: int


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
    effective_time_select = "COALESCE(scan_cycle_at, created_at)" if "scan_cycle_at" in columns else "created_at"
    rows = conn.execute(
        f"""
        SELECT
            session_date,
            ticker,
            {effective_time_select} AS created_at,
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
        ORDER BY session_date, {effective_time_select}, ticker
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


def _passes_prev_scan_jump_filter(
    row: TraceRow,
    previous_scan_prices: dict[str, int],
    max_intraday_jump_from_prev_scan_percent: float,
) -> bool:
    threshold_percent = float(max_intraday_jump_from_prev_scan_percent or 0.0)
    if threshold_percent <= 0:
        return True
    current_price = row.current_price or row.price
    if current_price <= 0:
        return False
    previous_price = int(previous_scan_prices.get(_ticker_key(row.ticker), 0) or 0)
    if previous_price <= 0:
        return True
    jump_percent = ((current_price - previous_price) / previous_price) * 100
    return jump_percent < threshold_percent


def load_selected_signals(db_path: Path) -> list[SelectedSignal]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(signals)").fetchall()
    }
    effective_time_select = "COALESCE(scan_cycle_at, created_at)" if "scan_cycle_at" in columns else "created_at"
    rows = conn.execute(
        f"""
        SELECT
            ticker,
            {effective_time_select} AS created_at,
            price,
            expect_price,
            expect_revenue_percent,
            spread_percent
        FROM signals
        WHERE selected = 1
        ORDER BY {effective_time_select}, ticker
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


def _load_buy_fill_rows(logs_dir: Path | None) -> list[dict[str, str]]:
    if logs_dir is None or not logs_dir.exists():
        return []

    fill_rows: list[dict[str, str]] = []
    fill_paths = sorted(logs_dir.glob("fills_*.csv"))
    for fill_path in fill_paths:
        with fill_path.open("r", newline="", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                side = str(row.get("side") or "").strip().upper()
                ticker = str(row.get("ticker") or "").strip()
                filled_at = str(row.get("filled_at") or "").strip().replace("T", " ")
                if side != "BUY" or not ticker or not filled_at:
                    continue
                fill_rows.append(
                    {
                        "side": side,
                        "ticker": ticker,
                        "filled_at": filled_at,
                        "trade_date": filled_at[:10],
                    }
                )

    if fill_rows:
        return fill_rows

    audit_path = logs_dir / "trade_fills_audit_daily.csv"
    if not audit_path.exists():
        return []

    with audit_path.open("r", newline="", encoding="utf-8-sig") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            side = str(row.get("side") or "").strip().upper()
            ticker = str(row.get("ticker") or "").strip()
            filled_at = str(row.get("filled_at") or "").strip().replace("T", " ")
            trade_date = str(row.get("trade_date") or "").strip()
            if side != "BUY" or not ticker or not filled_at or not trade_date:
                continue
            fill_rows.append(
                {
                    "side": side,
                    "ticker": ticker,
                    "filled_at": filled_at,
                    "trade_date": trade_date,
                }
            )
    return fill_rows


def load_actual_exit_overrides_from_fills(
    logs_dir: Path | None,
) -> dict[tuple[str, str], list[ActualExitOverride]]:
    if logs_dir is None or not logs_dir.exists():
        return {}

    fill_events: list[dict[str, str | int]] = []
    for fill_path in sorted(logs_dir.glob("fills_*.csv")):
        with fill_path.open("r", newline="", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                ticker = str(row.get("ticker") or "").strip()
                side = str(row.get("side") or "").strip().upper()
                filled_at = str(row.get("filled_at") or "").strip().replace("T", " ")
                price = _to_int(row.get("price"))
                quantity = _to_int(row.get("quantity"))
                if not ticker or side not in {"BUY", "SELL"} or not filled_at or price <= 0 or quantity <= 0:
                    continue
                fill_events.append(
                    {
                        "session_date": filled_at[:10],
                        "ticker": ticker,
                        "side": side,
                        "filled_at": filled_at,
                        "price": price,
                        "quantity": quantity,
                    }
                )

    fill_events.sort(key=lambda item: (str(item["filled_at"]), str(item["ticker"]), str(item["side"])))

    open_positions: dict[tuple[str, str], list[dict[str, object]]] = {}
    overrides: dict[tuple[str, str], list[ActualExitOverride]] = {}
    for event in fill_events:
        session_date = str(event["session_date"])
        ticker = str(event["ticker"])
        key = (session_date, ticker)
        if event["side"] == "BUY":
            open_positions.setdefault(key, []).append(
                {
                    "buy_filled_at": str(event["filled_at"]),
                    "entry_price": int(event["price"]),
                    "quantity": int(event["quantity"]),
                    "remaining_quantity": int(event["quantity"]),
                    "sell_fills": [],
                }
            )
            continue

        pending_positions = open_positions.get(key, [])
        sell_quantity_remaining = int(event["quantity"])
        while sell_quantity_remaining > 0 and pending_positions:
            pending = pending_positions[0]
            remaining_quantity = int(pending["remaining_quantity"])
            matched_quantity = min(remaining_quantity, sell_quantity_remaining)
            pending["sell_fills"].append(
                {
                    "filled_at": str(event["filled_at"]),
                    "price": int(event["price"]),
                    "quantity": matched_quantity,
                }
            )
            remaining_quantity -= matched_quantity
            sell_quantity_remaining -= matched_quantity
            pending["remaining_quantity"] = remaining_quantity
            if remaining_quantity > 0:
                break

            sell_fills = list(pending["sell_fills"])
            total_sold_quantity = sum(int(fill["quantity"]) for fill in sell_fills)
            weighted_exit_value = sum(int(fill["price"]) * int(fill["quantity"]) for fill in sell_fills)
            overrides.setdefault(key, []).append(
                ActualExitOverride(
                    session_date=session_date,
                    ticker=ticker,
                    buy_filled_at=str(pending["buy_filled_at"]),
                    entry_price=int(pending["entry_price"]),
                    quantity=int(pending["quantity"]),
                    final_exit_time=str(sell_fills[-1]["filled_at"]),
                    weighted_exit_price=int(round(weighted_exit_value / total_sold_quantity)),
                )
            )
            pending_positions.pop(0)

    for key in overrides:
        overrides[key].sort(key=lambda item: item.buy_filled_at)
    return overrides


def infer_selected_signals_from_fill_audit(
    db_path: Path,
    logs_dir: Path | None,
    max_fill_lag_seconds: int = 180,
) -> list[SelectedSignal]:
    fill_rows = _load_buy_fill_rows(logs_dir)
    if not fill_rows:
        return []

    traces = load_traces(db_path)
    scan_rows_by_day_and_ticker: dict[tuple[str, str], list[TraceRow]] = {}
    for row in traces:
        if row.phase != "scan_candidate":
            continue
        scan_rows_by_day_and_ticker.setdefault((row.session_date, row.ticker), []).append(row)

    for rows in scan_rows_by_day_and_ticker.values():
        rows.sort(key=lambda item: item.created_at)

    inferred: list[SelectedSignal] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for row in fill_rows:
        ticker = row["ticker"]
        filled_at = row["filled_at"]
        trade_date = row["trade_date"]
        trace_rows = scan_rows_by_day_and_ticker.get((trade_date, ticker))
        if not trace_rows:
            continue
        try:
            filled_dt = _parse_timestamp(filled_at)
        except ValueError:
            continue

        matched_row: TraceRow | None = None
        for trace_row in trace_rows:
            try:
                trace_dt = _parse_timestamp(trace_row.created_at)
            except ValueError:
                continue
            lag_seconds = (filled_dt - trace_dt).total_seconds()
            if lag_seconds < 0 or lag_seconds > max_fill_lag_seconds:
                continue
            matched_row = trace_row

        if matched_row is None:
            continue
        signal_key = (matched_row.session_date, matched_row.ticker, matched_row.created_at)
        if signal_key in seen_keys:
            continue
        seen_keys.add(signal_key)
        inferred.append(
            SelectedSignal(
                session_date=matched_row.session_date,
                ticker=matched_row.ticker,
                created_at=matched_row.created_at,
                price=matched_row.current_price or matched_row.price,
                expect_price=matched_row.expect_price,
                expect_revenue_percent=matched_row.expect_revenue_percent,
                spread_percent=matched_row.spread_percent,
            )
        )
    return sorted(inferred, key=lambda item: (item.created_at, item.ticker))


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


def _is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_trend_ok_tickers_by_day(logs_dir: Path) -> tuple[dict[str, set[str]], set[str]]:
    trend_ok_by_day: dict[str, set[str]] = {}
    covered_days: set[str] = set()
    if not logs_dir.exists():
        return trend_ok_by_day, covered_days

    for path in sorted(logs_dir.glob("daily_reference_prices_*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                session_date = str(row.get("session_date") or "").strip()
                ticker = str(row.get("ticker") or "").strip()
                if not session_date or not ticker:
                    continue
                covered_days.add(session_date)
                if _is_truthy(row.get("trend_ok")):
                    trend_ok_by_day.setdefault(session_date, set()).add(ticker)
    return trend_ok_by_day, covered_days


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


def _realized_pnl_percent(entry_price: int, exit_price: int) -> float:
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100


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


def _selected_tickers_by_timestamp(selected_signals: list[SelectedSignal]) -> dict[tuple[str, str], set[str]]:
    by_timestamp: dict[tuple[str, str], set[str]] = {}
    for signal in selected_signals:
        by_timestamp.setdefault((signal.session_date, signal.created_at), set()).add(signal.ticker)
    return by_timestamp


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
    trend_allowed_tickers: set[str] | None,
    previous_scan_prices: dict[str, int],
    max_intraday_jump_from_prev_scan_percent: float,
) -> list[TraceRow]:
    latest_by_ticker: dict[str, TraceRow] = {}
    for row in rows:
        if row.phase != "scan_candidate":
            continue
        if row.ticker in active_tickers:
            continue
        if allowed_tickers is not None and row.ticker not in allowed_tickers:
            continue
        if trend_allowed_tickers is not None and row.ticker not in trend_allowed_tickers:
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
        if not _passes_prev_scan_jump_filter(
            row,
            previous_scan_prices=previous_scan_prices,
            max_intraday_jump_from_prev_scan_percent=max_intraday_jump_from_prev_scan_percent,
        ):
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


def _pick_candidates_for_entry_with_fallback(
    rows: list[TraceRow],
    min_expected_return_percent: float,
    fallback_min_expected_return_percents: list[float] | tuple[float, ...] | None,
    max_spread_percent: float,
    top_ratio: float,
    spread_expected_return_multiplier: float,
    min_prev_day_change_percent: float,
    max_prev_day_change_percent: float,
    active_tickers: set[str],
    allowed_tickers: set[str] | None,
    trend_allowed_tickers: set[str] | None,
    previous_scan_prices: dict[str, int],
    max_intraday_jump_from_prev_scan_percent: float,
) -> tuple[list[TraceRow], float]:
    candidates = _pick_candidates_for_timestamp(
        rows=rows,
        min_expected_return_percent=min_expected_return_percent,
        max_spread_percent=max_spread_percent,
        top_ratio=top_ratio,
        spread_expected_return_multiplier=spread_expected_return_multiplier,
        min_prev_day_change_percent=min_prev_day_change_percent,
        max_prev_day_change_percent=max_prev_day_change_percent,
        active_tickers=active_tickers,
        allowed_tickers=allowed_tickers,
        trend_allowed_tickers=trend_allowed_tickers,
        previous_scan_prices=previous_scan_prices,
        max_intraday_jump_from_prev_scan_percent=max_intraday_jump_from_prev_scan_percent,
    )
    if candidates:
        return candidates, min_expected_return_percent
    if active_tickers:
        return candidates, min_expected_return_percent
    for fallback_threshold in fallback_min_expected_return_percents or []:
        if fallback_threshold <= 0 or fallback_threshold >= min_expected_return_percent:
            continue
        fallback_candidates = _pick_candidates_for_timestamp(
            rows=rows,
            min_expected_return_percent=fallback_threshold,
            max_spread_percent=max_spread_percent,
            top_ratio=top_ratio,
            spread_expected_return_multiplier=spread_expected_return_multiplier,
            min_prev_day_change_percent=min_prev_day_change_percent,
            max_prev_day_change_percent=max_prev_day_change_percent,
            active_tickers=active_tickers,
            allowed_tickers=allowed_tickers,
            trend_allowed_tickers=trend_allowed_tickers,
            previous_scan_prices=previous_scan_prices,
            max_intraday_jump_from_prev_scan_percent=max_intraday_jump_from_prev_scan_percent,
        )
        if fallback_candidates:
            return fallback_candidates, fallback_threshold
    return candidates, min_expected_return_percent


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
    trend_ok_tickers_by_day: dict[str, set[str]] | None = None,
    trend_filter_days: set[str] | None = None,
) -> dict[str, list[TraceRow]]:
    if selected_signals:
        per_day: dict[str, list[TraceRow]] = {}
        for signal in selected_signals:
            if trend_filter_days and signal.session_date in trend_filter_days:
                trend_allowed_tickers = (trend_ok_tickers_by_day or {}).get(signal.session_date, set())
                if signal.ticker not in trend_allowed_tickers:
                    continue
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
        if trend_filter_days and first.session_date in trend_filter_days:
            trend_allowed_tickers = (trend_ok_tickers_by_day or {}).get(first.session_date, set())
            if first.ticker not in trend_allowed_tickers:
                continue
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
    max_hold_seconds_before_exit: int = 0,
    spread_expected_return_multiplier: float = 0.0,
    max_intraday_jump_from_prev_scan_percent: float = 0.0,
    fallback_min_expected_return_percents: list[float] | tuple[float, ...] | None = None,
    max_orderbook_ask_depth_ratio: float = 0.0,
    missing_ask_depth_policy: str = "ignore",
    allow_refill_empty_slots: bool = False,
    trend_filter_enabled: bool = False,
    trend_ok_tickers_by_day: dict[str, set[str]] | None = None,
    trend_filter_days: set[str] | None = None,
    selected_signals_override: list[SelectedSignal] | None = None,
    actual_exit_overrides_by_ticker: dict[tuple[str, str], list[ActualExitOverride]] | None = None,
) -> list[BacktestTrade]:
    traces = load_traces(db_path)
    selected_signals = (
        list(selected_signals_override)
        if selected_signals_override is not None
        else (load_selected_signals(db_path) if use_selected_signals else [])
    )
    selected_tickers = _selected_tickers_by_day(selected_signals)
    selected_tickers_by_timestamp = _selected_tickers_by_timestamp(selected_signals)
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
        actual_exit_queues: dict[tuple[str, str], list[ActualExitOverride]] = {}
        session_override_times: set[str] = set()
        if actual_exit_overrides_by_ticker:
            for key, overrides in actual_exit_overrides_by_ticker.items():
                if key[0] != session_date or not overrides:
                    continue
                actual_exit_queues[key] = list(overrides)
                session_override_times.update(item.final_exit_time for item in overrides)

        open_positions: dict[str, ReplayPosition] = {}
        previous_scan_prices: dict[str, int] = {}
        trend_allowed_tickers = None
        if trend_filter_enabled and trend_filter_days and session_date in trend_filter_days:
            trend_allowed_tickers = (trend_ok_tickers_by_day or {}).get(session_date, set())
        available_cash = session_plan.session_capital_basis

        for created_at in sorted(set(rows_by_time) | session_override_times):
            rows_at_time = rows_by_time.get(created_at, [])
            rows_by_ticker = {row.ticker: row for row in rows_at_time}
            exited_tickers_at_time: set[str] = set()

            for ticker, position in list(open_positions.items()):
                actual_exit_override = position.actual_exit_override
                if actual_exit_override is not None and actual_exit_override.final_exit_time <= created_at:
                    exit_price = actual_exit_override.weighted_exit_price
                    trades.append(
                        BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=actual_exit_override.final_exit_time,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=exit_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * exit_price,
                            exit_reason="actual_fill_exit",
                            pnl_percent=_realized_pnl_percent(position.entry_price, exit_price),
                        )
                    )
                    available_cash += position.quantity * exit_price
                    del open_positions[ticker]
                    exited_tickers_at_time.add(ticker)
                    continue
                if actual_exit_override is not None:
                    continue
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
                if max_hold_seconds_before_exit > 0:
                    held_seconds = (_parse_timestamp(current_row.created_at) - _parse_timestamp(position.entry.created_at)).total_seconds()
                    if held_seconds > max_hold_seconds_before_exit:
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
                                exit_reason="time_stop_loss",
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
                    exit_price = position.target_price
                    trades.append(
                        BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=exit_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * exit_price,
                            exit_reason="take_profit",
                            pnl_percent=_realized_pnl_percent(position.entry_price, exit_price),
                        )
                    )
                    available_cash += position.quantity * exit_price
                    del open_positions[ticker]
                    exited_tickers_at_time.add(ticker)

            if not _is_within_buy_window(created_at, start_buy_time, stop_buy_time):
                scan_rows = [row for row in rows_at_time if row.phase == "scan_candidate"]
                if scan_rows:
                    previous_scan_prices = {
                        _ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                        for row in scan_rows
                        if int((row.current_price or row.price) or 0) > 0
                    }
                continue

            scan_rows = [row for row in rows_at_time if row.phase == "scan_candidate"]
            if not scan_rows:
                continue
            allowed_tickers = None
            if use_selected_signals:
                allowed_tickers = selected_tickers_by_timestamp.get((session_date, created_at))
                if allowed_tickers is None:
                    previous_scan_prices = {
                        _ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                        for row in scan_rows
                        if int((row.current_price or row.price) or 0) > 0
                    }
                    continue
            effective_trend_allowed_tickers = trend_allowed_tickers
            if effective_trend_allowed_tickers is not None and allowed_tickers is not None:
                effective_trend_allowed_tickers = effective_trend_allowed_tickers & allowed_tickers

            # Match the live bot's batch flow: once any slot is occupied,
            # block new entries until the whole set is flat again.
            if not allow_refill_empty_slots and open_positions:
                previous_scan_prices = {
                    _ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                    for row in scan_rows
                    if int((row.current_price or row.price) or 0) > 0
                }
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
                previous_scan_prices = {
                    _ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                    for row in scan_rows
                    if int((row.current_price or row.price) or 0) > 0
                }
                continue

            candidates, used_threshold = _pick_candidates_for_entry_with_fallback(
                scan_rows,
                min_expected_return_percent=min_expected_return_percent,
                fallback_min_expected_return_percents=fallback_min_expected_return_percents,
                max_spread_percent=max_spread_percent,
                top_ratio=top_ratio,
                spread_expected_return_multiplier=spread_expected_return_multiplier,
                min_prev_day_change_percent=min_prev_day_change_percent,
                max_prev_day_change_percent=max_prev_day_change_percent,
                active_tickers=set(open_positions) | exited_tickers_at_time,
                allowed_tickers=allowed_tickers,
                trend_allowed_tickers=effective_trend_allowed_tickers,
                previous_scan_prices=previous_scan_prices,
                max_intraday_jump_from_prev_scan_percent=max_intraday_jump_from_prev_scan_percent,
            )
            if candidates and used_threshold != min_expected_return_percent:
                print(
                    f"Replay fallback threshold used for {session_date} {created_at}: "
                    f"{used_threshold:.2f} instead of {min_expected_return_percent:.2f}; "
                    f"candidates={len(candidates)}"
                )
            previous_scan_prices = {
                _ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                for row in scan_rows
                if int((row.current_price or row.price) or 0) > 0
            }

            candidate_models: list[Candidate] = []
            candidate_rows_by_ticker: dict[str, TraceRow] = {}
            for candidate in candidates:
                entry_price = candidate.current_price or candidate.price
                if entry_price <= 0:
                    continue
                if max_orderbook_ask_depth_ratio > 0 and candidate.ask_depth_5_amount_krw <= 0 and missing_ask_depth_policy == "skip":
                    continue
                candidate_models.append(
                    Candidate(
                        ticker=candidate.ticker,
                        price=entry_price,
                        expect_price=candidate.expect_price,
                        expect_revenue_percent=candidate.expect_revenue_percent,
                        spread_percent=candidate.spread_percent,
                        ask_depth_5_amount_krw=candidate.ask_depth_5_amount_krw,
                    )
                )
                candidate_rows_by_ticker[candidate.ticker] = candidate

            selected_targets = select_affordable_targets(
                candidate_models,
                max_buy_count=planned_buy_count,
                available_cash_krw=available_cash,
                budget_per_stock_krw=session_plan.slot_budget_per_stock,
                sell_tick_offset=sell_tick_offset,
                max_orderbook_ask_depth_ratio=max_orderbook_ask_depth_ratio,
            )

            for candidate_model in selected_targets:
                candidate = candidate_rows_by_ticker.get(candidate_model.ticker)
                if candidate is None:
                    continue
                entry_price = candidate.current_price or candidate.price
                quantity = calc_order_quantity(candidate_model, session_plan.slot_budget_per_stock)
                estimated_cost = quantity * entry_price
                if quantity <= 0 or estimated_cost <= 0 or estimated_cost > available_cash:
                    setattr(candidate_model, "planned_budget_krw", 0)
                    continue
                if max_orderbook_ask_depth_ratio > 0 and candidate.ask_depth_5_amount_krw > 0:
                    if not passes_orderbook_ask_depth_ratio(
                        candidate_model,
                        estimated_cost_krw=estimated_cost,
                        max_orderbook_ask_depth_ratio=max_orderbook_ask_depth_ratio,
                    ):
                        setattr(candidate_model, "planned_budget_krw", 0)
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
                    actual_exit_override=(
                        actual_exit_queues.get((session_date, candidate.ticker), []).pop(0)
                        if actual_exit_queues.get((session_date, candidate.ticker))
                        else None
                    ),
                )
                available_cash -= estimated_cost
                setattr(candidate_model, "planned_budget_krw", 0)

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


def _load_backtest_default_config(config_path: str | Path) -> dict:
    cfg = load_yaml(config_path)
    return cfg if isinstance(cfg, dict) else {}


def parse_args():
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", default=str(ROOT.parent / "config/settings.yaml"))
    config_args, remaining_argv = config_parser.parse_known_args()
    cfg = _load_backtest_default_config(config_args.config)
    market_cfg = cfg.get("market", {})
    strategy_cfg = cfg.get("strategy", {})
    risk_cfg = cfg.get("risk", {})
    trend_cfg = cfg.get("trend_filter", {})
    default_fallback_thresholds = resolve_fallback_expected_return_thresholds(
        strategy_cfg,
        primary_threshold=float(strategy_cfg.get("min_expected_return_percent", 0.71) or 0.71),
    )

    parser = argparse.ArgumentParser(
        description="Replay Daily_bot market_traces from bot.sqlite3.",
        parents=[config_parser],
    )
    parser.add_argument("--db", default="bot.sqlite3")
    parser.add_argument("--logs-dir", default="")
    parser.add_argument("--min-expected-return", type=float, default=float(strategy_cfg.get("min_expected_return_percent", 0.71) or 0.71))
    parser.add_argument(
        "--fallback-min-expected-return",
        type=float,
        action="append",
        dest="fallback_min_expected_returns",
        default=None,
    )
    parser.add_argument("--fallback-min-expected-returns", dest="fallback_min_expected_returns_csv", default="")
    parser.add_argument("--max-spread", type=float, default=float(strategy_cfg.get("max_spread_percent", 0.0) or 0.0))
    parser.add_argument("--min-prev-day-change", type=float, default=float(strategy_cfg.get("min_prev_day_change_percent", 0.0) or 0.0))
    parser.add_argument("--max-prev-day-change", type=float, default=float(strategy_cfg.get("max_prev_day_change_percent", 0.0) or 0.0))
    parser.add_argument(
        "--max-intraday-jump-from-prev-scan",
        type=float,
        default=float(strategy_cfg.get("max_intraday_jump_from_prev_scan_percent", 0.0) or 0.0),
    )
    parser.add_argument("--top-n", type=int, default=0)
    parser.add_argument("--top-ratio", type=float, default=float(strategy_cfg.get("top_ratio", 1.0) or 1.0))
    parser.add_argument("--take-profit", type=float, default=0.4)
    parser.add_argument("--stop-loss", type=float, default=float(risk_cfg.get("stop_loss_percent", 4.5) or 4.5))
    parser.add_argument("--stop-loss-tick-count", type=int, default=int(risk_cfg.get("stop_loss_tick_count", 0) or 0))
    parser.add_argument("--stop-loss-tick-multiplier", type=float, default=float(risk_cfg.get("stop_loss_tick_multiplier", 0.0) or 0.0))
    parser.add_argument("--sell-tick-offset", type=int, default=int(strategy_cfg.get("sell_tick_offset", 1) or 1))
    parser.add_argument("--start-buy-time", default=str(market_cfg.get("start_buy_time", "09:30") or "09:30"))
    parser.add_argument("--stop-buy-time", default=str(market_cfg.get("stop_buy_time", "11:30") or "11:30"))
    parser.add_argument("--force-sell-time", default=str(market_cfg.get("force_sell_time", "15:00") or "15:00"))
    parser.add_argument("--max-hold-seconds-before-exit", type=int, default=0)
    parser.add_argument(
        "--spread-expected-return-multiplier",
        type=float,
        default=float(strategy_cfg.get("spread_expected_return_multiplier", 0.0) or 0.0),
    )
    parser.add_argument(
        "--max-orderbook-ask-depth-ratio",
        type=float,
        default=float(risk_cfg.get("max_orderbook_ask_depth_ratio", 0.0) or 0.0),
    )
    parser.add_argument("--missing-ask-depth-policy", choices=["ignore", "skip"], default="ignore")
    parser.add_argument("--trend-filter-enabled", dest="trend_filter_enabled", action="store_true")
    parser.add_argument("--trend-filter-disabled", dest="trend_filter_enabled", action="store_false")
    parser.set_defaults(trend_filter_enabled=bool(trend_cfg.get("enabled", False)))
    parser.add_argument("--starting-capital-krw", type=int, default=1_000_000)
    parser.add_argument("--min-slot-count", type=int, default=int(risk_cfg.get("min_slot_count", 1) or 1))
    parser.add_argument("--max-slot-count", type=int, default=int(risk_cfg.get("max_slot_count", 0) or 0))
    parser.add_argument("--slot-budget-unit-krw", type=int, default=int(risk_cfg.get("slot_budget_unit_krw", 0) or 0))
    parser.add_argument("--max-budget-per-stock-krw", type=int, default=int(risk_cfg.get("max_budget_per_stock_krw", 0) or 0))
    parser.add_argument(
        "--max-position-count",
        type=int,
        default=int(risk_cfg.get("max_position_count", strategy_cfg.get("max_buy_count", 0)) or 0),
    )
    parser.add_argument(
        "--target-budget-ratio-per-stock",
        type=float,
        default=float(risk_cfg.get("target_budget_ratio_per_stock", 0.0) or 0.0),
    )
    parser.add_argument("--out", default="Daily_bot/backtest/results/backtest_replay.csv")
    parser.add_argument("--use-selected-signals", dest="use_selected_signals", action="store_true")
    parser.add_argument("--ignore-selected-signals", dest="use_selected_signals", action="store_false")
    parser.set_defaults(use_selected_signals=False)
    parser.add_argument("--use-actual-fill-exits", dest="use_actual_fill_exits", action="store_true")
    parser.add_argument("--ignore-actual-fill-exits", dest="use_actual_fill_exits", action="store_false")
    parser.set_defaults(use_actual_fill_exits=False)
    parser.add_argument("--allow-refill-empty-slots", dest="allow_refill_empty_slots", action="store_true")
    parser.add_argument("--disallow-refill-empty-slots", dest="allow_refill_empty_slots", action="store_false")
    parser.set_defaults(allow_refill_empty_slots=False)
    args = parser.parse_args(remaining_argv)

    resolved_fallback_thresholds = list(args.fallback_min_expected_returns or [])
    if args.fallback_min_expected_returns_csv:
        for raw_value in str(args.fallback_min_expected_returns_csv).split(","):
            raw_value = raw_value.strip()
            if not raw_value:
                continue
            resolved_fallback_thresholds.append(float(raw_value))
    if args.fallback_min_expected_returns is None and not args.fallback_min_expected_returns_csv:
        resolved_fallback_thresholds = list(default_fallback_thresholds)

    filtered_thresholds: list[float] = []
    for threshold in resolved_fallback_thresholds:
        if threshold <= 0 or threshold >= args.min_expected_return or threshold in filtered_thresholds:
            continue
        filtered_thresholds.append(threshold)
    args.fallback_min_expected_returns = filtered_thresholds
    args.fallback_min_expected_return = filtered_thresholds[0] if filtered_thresholds else 0.0
    return args


if __name__ == "__main__":
    args = parse_args()
    logs_dir = Path(args.logs_dir) if args.logs_dir else None
    resolved_db_path = resolve_replay_db_path(
        Path(args.db),
        logs_dir,
    )
    if resolved_db_path != Path(args.db):
        print(f"Rebuilt replay DB from logs: {resolved_db_path}")
    session_capital_by_day = load_session_capital_bases(resolved_db_path)
    trend_ok_tickers_by_day, trend_filter_days = load_trend_ok_tickers_by_day(logs_dir) if args.trend_filter_enabled and logs_dir else ({}, set())
    selected_signals_override = None
    actual_exit_overrides_by_ticker = (
        load_actual_exit_overrides_from_fills(logs_dir)
        if args.use_actual_fill_exits
        else None
    )
    if args.use_selected_signals:
        loaded_selected_signals = load_selected_signals(resolved_db_path)
        if loaded_selected_signals:
            selected_signals_override = loaded_selected_signals
        else:
            inferred_selected_signals = infer_selected_signals_from_fill_audit(
                db_path=resolved_db_path,
                logs_dir=logs_dir,
            )
            if inferred_selected_signals:
                selected_signals_override = inferred_selected_signals
                print(
                    "Inferred selected signals from fill logs "
                    f"because signals table was empty: {len(inferred_selected_signals)} rows"
                )
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
        use_selected_signals=args.use_selected_signals,
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
        max_hold_seconds_before_exit=args.max_hold_seconds_before_exit,
        spread_expected_return_multiplier=args.spread_expected_return_multiplier,
        max_intraday_jump_from_prev_scan_percent=args.max_intraday_jump_from_prev_scan,
        fallback_min_expected_return_percents=args.fallback_min_expected_returns,
        max_orderbook_ask_depth_ratio=args.max_orderbook_ask_depth_ratio,
        missing_ask_depth_policy=args.missing_ask_depth_policy,
        allow_refill_empty_slots=args.allow_refill_empty_slots,
        trend_filter_enabled=args.trend_filter_enabled,
        trend_ok_tickers_by_day=trend_ok_tickers_by_day,
        trend_filter_days=trend_filter_days,
        selected_signals_override=selected_signals_override,
        actual_exit_overrides_by_ticker=actual_exit_overrides_by_ticker,
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
