from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from Daily_bot.models import Fill


DEFAULT_FEE_RATE = 0.00015
DEFAULT_SELL_TAX_RATE = 0.0018

AUDIT_FILL_FIELDNAMES = [
    "trade_date",
    "filled_at",
    "broker_order_id",
    "ticker",
    "side",
    "quantity",
    "price",
    "amount",
    "estimated_fee",
    "estimated_tax",
    "estimated_total_cost",
    "source",
    "cash",
    "account_value",
    "adjusted_account_value",
    "adjusted_pnl",
    "loss_percent",
    "kospi_change_percent",
    "cum_buy_quantity",
    "cum_buy_amount",
    "avg_buy_price",
    "cum_sell_quantity",
    "cum_sell_amount",
    "realized_pnl_before_costs",
    "estimated_net_realized_pnl",
    "realized_return_percent_before_costs",
    "estimated_net_realized_return_percent",
    "position_status",
]

AUDIT_EXCLUDED_FILL_SOURCES = {
    "position_recovery",
    "sell_reconciliation",
}


def _to_float(value: int | float | str | None) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _format_percent(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"


def _read_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as fp:
        return list(csv.DictReader(fp))


def should_include_in_fill_audit(source: str) -> bool:
    return str(source or "").strip() not in AUDIT_EXCLUDED_FILL_SOURCES


def _append_audit_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    should_write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=AUDIT_FILL_FIELDNAMES)
        if should_write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in AUDIT_FILL_FIELDNAMES})


def _build_running_state(rows: list[dict[str, str]], ticker: str) -> dict[str, float]:
    state = {
        "buy_qty": 0.0,
        "buy_amount": 0.0,
        "sell_qty": 0.0,
        "sell_amount": 0.0,
        "total_fee": 0.0,
        "total_tax": 0.0,
        "total_cost": 0.0,
    }
    for row in rows:
        if str(row.get("ticker", "")).strip() != ticker:
            continue
        state["buy_qty"] = _to_float(row.get("cum_buy_quantity"))
        state["buy_amount"] = _to_float(row.get("cum_buy_amount"))
        state["sell_qty"] = _to_float(row.get("cum_sell_quantity"))
        state["sell_amount"] = _to_float(row.get("cum_sell_amount"))
        state["total_fee"] += _to_float(row.get("estimated_fee"))
        state["total_tax"] += _to_float(row.get("estimated_tax"))
        state["total_cost"] += _to_float(row.get("estimated_total_cost"))
    return state


def _snapshot_value(account_snapshot: dict[str, Any] | None, key: str) -> Any:
    if not account_snapshot:
        return ""
    value = account_snapshot.get(key, "")
    return "" if value is None else value


def extract_fill_costs(fill: Fill, side_upper: str) -> tuple[float | None, float | None]:
    raw = fill.raw if isinstance(fill.raw, dict) else {}
    rows = []
    if isinstance(raw.get("rows"), list):
        rows = [row for row in raw["rows"] if isinstance(row, dict)]
    latest_row = raw.get("latest_row")
    if isinstance(latest_row, dict):
        rows.append(latest_row)
    if not rows and raw:
        rows = [raw]

    seen: set[tuple[str, str, str]] = set()
    total_fee = 0.0
    total_tax = 0.0
    found = False
    for row in rows:
        key = (
            str(row.get("ord_no") or ""),
            str(row.get("cntr_no") or ""),
            str(row.get("ord_tm") or row.get("tm") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        fee = _to_float(row.get("tdy_trde_cmsn"))
        tax = _to_float(row.get("tdy_trde_tax"))
        if fee or tax or "tdy_trde_cmsn" in row or "tdy_trde_tax" in row:
            found = True
            total_fee += fee
            total_tax += tax
    if not found:
        return None, None
    if side_upper != "SELL":
        total_tax = 0.0
    return total_fee, total_tax


def estimate_fill_costs(
    fill: Fill,
    side_upper: str,
    fee_rate: float = DEFAULT_FEE_RATE,
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE,
) -> tuple[float, float]:
    quantity = int(fill.quantity or 0)
    price = int(fill.price or 0)
    amount = quantity * price
    actual_fee, actual_tax = extract_fill_costs(fill, side_upper)
    estimated_fee = round(actual_fee, 4) if actual_fee is not None else round(amount * fee_rate, 4)
    estimated_tax = (
        round(actual_tax, 4)
        if actual_tax is not None
        else (round(amount * sell_tax_rate, 4) if side_upper == "SELL" else 0.0)
    )
    return estimated_fee, estimated_tax


def append_fill_audit_csv(
    path: Path,
    fill: Fill,
    side: str,
    source: str,
    account_snapshot: dict[str, Any] | None = None,
    fee_rate: float = DEFAULT_FEE_RATE,
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE,
) -> None:
    """Append one fill to a single Excel-friendly audit CSV.

    This file is intentionally append-only and formula-friendly so that family
    or third-party reviewers can open it in Excel and verify fills with filters,
    formulas, or pivot tables without inspecting SQLite.
    """
    side_upper = str(side or "").strip().upper()
    ticker = str(fill.ticker or "").strip()
    quantity = int(fill.quantity or 0)
    price = int(fill.price or 0)
    amount = quantity * price
    estimated_fee, estimated_tax = estimate_fill_costs(
        fill,
        side_upper,
        fee_rate=fee_rate,
        sell_tax_rate=sell_tax_rate,
    )
    estimated_total_cost = estimated_fee + estimated_tax
    filled_at = fill.filled_at if fill.filled_at is not None else datetime.now()

    rows = _read_existing_rows(path)
    state = _build_running_state(rows, ticker)

    if side_upper == "BUY":
        state["buy_qty"] += quantity
        state["buy_amount"] += amount
    elif side_upper == "SELL":
        state["sell_qty"] += quantity
        state["sell_amount"] += amount
    state["total_fee"] += estimated_fee
    state["total_tax"] += estimated_tax
    state["total_cost"] += estimated_total_cost

    avg_buy_price = state["buy_amount"] / state["buy_qty"] if state["buy_qty"] > 0 else 0.0
    matched_sell_qty = min(state["sell_qty"], state["buy_qty"])
    realized_pnl_before_costs = state["sell_amount"] - (avg_buy_price * matched_sell_qty)
    estimated_net_realized_pnl = realized_pnl_before_costs - state["total_cost"]
    realized_return_percent_before_costs = (
        realized_pnl_before_costs / (avg_buy_price * matched_sell_qty) * 100
        if avg_buy_price > 0 and matched_sell_qty > 0
        else None
    )
    estimated_net_realized_return_percent = (
        estimated_net_realized_pnl / (avg_buy_price * matched_sell_qty) * 100
        if avg_buy_price > 0 and matched_sell_qty > 0
        else None
    )
    remaining_qty = state["buy_qty"] - state["sell_qty"]
    position_status = "CLOSED" if state["buy_qty"] > 0 and remaining_qty <= 0 else "OPEN"

    _append_audit_row(
        path,
        {
            "trade_date": filled_at.strftime("%Y-%m-%d"),
            "filled_at": filled_at.isoformat(),
            "broker_order_id": fill.order_id,
            "ticker": ticker,
            "side": side_upper,
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "estimated_fee": estimated_fee,
            "estimated_tax": estimated_tax,
            "estimated_total_cost": estimated_total_cost,
            "source": source,
            "cash": _snapshot_value(account_snapshot, "cash"),
            "account_value": _snapshot_value(account_snapshot, "account_value"),
            "adjusted_account_value": _snapshot_value(account_snapshot, "adjusted_account_value"),
            "adjusted_pnl": _snapshot_value(account_snapshot, "adjusted_pnl"),
            "loss_percent": _snapshot_value(account_snapshot, "loss_percent"),
            "kospi_change_percent": _snapshot_value(account_snapshot, "kospi_change_percent"),
            "cum_buy_quantity": int(state["buy_qty"]),
            "cum_buy_amount": int(state["buy_amount"]),
            "avg_buy_price": round(avg_buy_price, 4) if avg_buy_price else "",
            "cum_sell_quantity": int(state["sell_qty"]),
            "cum_sell_amount": int(state["sell_amount"]),
            "realized_pnl_before_costs": round(realized_pnl_before_costs, 4) if matched_sell_qty > 0 else "",
            "estimated_net_realized_pnl": round(estimated_net_realized_pnl, 4) if matched_sell_qty > 0 else "",
            "realized_return_percent_before_costs": _format_percent(realized_return_percent_before_costs),
            "estimated_net_realized_return_percent": _format_percent(estimated_net_realized_return_percent),
            "position_status": position_status,
        },
    )


def rewrite_fill_audit_csv(
    path: Path,
    entries: list[tuple[Fill, str, str]],
    account_snapshots_by_order_id: dict[str, dict[str, Any] | None] | None = None,
    fee_rate: float = DEFAULT_FEE_RATE,
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE,
) -> None:
    if path.exists():
        path.unlink()

    snapshot_map = account_snapshots_by_order_id or {}
    ordered_entries = sorted(
        entries,
        key=lambda item: (item[0].filled_at, item[0].order_id, item[1], item[2]),
    )
    for fill, side, source in ordered_entries:
        append_fill_audit_csv(
            path,
            fill,
            side=side,
            source=source,
            account_snapshot=snapshot_map.get(fill.order_id),
            fee_rate=fee_rate,
            sell_tax_rate=sell_tax_rate,
        )
