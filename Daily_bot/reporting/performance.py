from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass
import json

from Daily_bot.models import Fill
from Daily_bot.storage.audit_csv import estimate_fill_costs
from Daily_bot.storage.audit_csv import should_include_in_fill_audit


@dataclass
class RealizedTrade:
    ticker: str
    quantity: int
    buy_price: int
    sell_price: int
    pnl_krw: int
    return_percent: float
    buy_filled_at: str
    sell_filled_at: str
    buy_fee_krw: float = 0.0
    sell_fee_krw: float = 0.0
    sell_tax_krw: float = 0.0
    net_pnl_krw: float = 0.0
    net_return_percent: float = 0.0


@dataclass
class PerformanceSummary:
    trade_count: int
    gross_pnl_krw: int
    wins: int
    losses: int
    breakeven: int
    open_buy_count: int
    open_buy_cost_krw: int
    trades: list[RealizedTrade]


@dataclass
class DailyRevenueSummary:
    session_date: str
    starting_capital_krw: int
    total_profit_krw: int
    total_fee_krw: int
    total_tax_krw: int
    total_buy_amount_krw: int
    total_sell_amount_krw: int
    total_return_percent: float
    total_return_percent_on_starting_capital: float
    traded_tickers: list[str]


@dataclass
class TradeEdgeSummary:
    trade_count: int
    wins: int
    losses: int
    breakeven: int
    actual_win_rate_percent: float
    gross_pnl_krw: int
    net_pnl_krw: int
    avg_gross_win_percent: float
    avg_gross_loss_percent: float
    avg_net_win_percent: float
    avg_net_loss_percent: float
    avg_gross_win_krw: float
    avg_gross_loss_krw: float
    avg_net_win_krw: float
    avg_net_loss_krw: float
    gross_payoff_ratio: float | None
    net_payoff_ratio: float | None
    gross_required_win_rate_percent: float | None
    net_required_win_rate_percent: float | None
    net_expectancy_krw: float
    net_expectancy_percent: float
    trades: list[RealizedTrade]


def load_realized_trades(db_path: str, session_date: str | None = None) -> list[RealizedTrade]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if session_date:
        rows = cur.execute(
            """
            select ticker, side, quantity, price, filled_at, created_at, source, raw_json
            from fills
            where substr(filled_at, 1, 10) = ?
            order by filled_at, id
            """,
            (session_date,),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            select ticker, side, quantity, price, filled_at, created_at, source, raw_json
            from fills
            order by filled_at, id
            """
        ).fetchall()

    conn.close()

    open_buys: dict[str, deque[dict[str, int | str]]] = defaultdict(deque)
    trades: list[RealizedTrade] = []

    for row in rows:
        side = str(row["side"] or "").upper()
        ticker = str(row["ticker"] or "")
        source = str(row["source"] or "")
        if not should_include_in_fill_audit(source):
            continue
        quantity = int(row["quantity"] or 0)
        price = int(row["price"] or 0)
        filled_at = str(row["filled_at"] or row["created_at"] or "")

        if side == "BUY":
            open_buys[ticker].append(
                {
                    "quantity": quantity,
                    "price": price,
                    "filled_at": filled_at,
                    "fee_per_share": 0.0,
                }
            )
            raw_json = str(row["raw_json"] or "")
            try:
                raw = json.loads(raw_json) if raw_json else None
            except json.JSONDecodeError:
                raw = {"raw_json": raw_json}
            fill = Fill(
                order_id="",
                ticker=ticker,
                quantity=quantity,
                price=price,
                raw=raw,
            )
            fee, _ = estimate_fill_costs(fill, side)
            if quantity > 0:
                open_buys[ticker][-1]["fee_per_share"] = fee / quantity
            continue

        if side != "SELL":
            continue

        raw_json = str(row["raw_json"] or "")
        try:
            raw = json.loads(raw_json) if raw_json else None
        except json.JSONDecodeError:
            raw = {"raw_json": raw_json}
        fill = Fill(
            order_id="",
            ticker=ticker,
            quantity=quantity,
            price=price,
            raw=raw,
        )
        sell_fee_total, sell_tax_total = estimate_fill_costs(fill, side)
        sell_fee_per_share = (sell_fee_total / quantity) if quantity > 0 else 0.0
        sell_tax_per_share = (sell_tax_total / quantity) if quantity > 0 else 0.0
        remaining = quantity
        while remaining > 0 and open_buys[ticker]:
            buy_lot = open_buys[ticker][0]
            matched = min(remaining, int(buy_lot["quantity"]))
            buy_price = int(buy_lot["price"])
            pnl_krw = (price - buy_price) * matched
            return_percent = ((price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
            buy_fee_krw = float(buy_lot.get("fee_per_share", 0.0)) * matched
            sell_fee_krw = sell_fee_per_share * matched
            sell_tax_krw = sell_tax_per_share * matched
            net_pnl_krw = pnl_krw - buy_fee_krw - sell_fee_krw - sell_tax_krw
            cost_basis = buy_price * matched
            net_return_percent = (net_pnl_krw / cost_basis * 100) if cost_basis > 0 else 0.0
            trades.append(
                RealizedTrade(
                    ticker=ticker,
                    quantity=matched,
                    buy_price=buy_price,
                    sell_price=price,
                    pnl_krw=pnl_krw,
                    return_percent=return_percent,
                    buy_filled_at=str(buy_lot["filled_at"]),
                    sell_filled_at=filled_at,
                    buy_fee_krw=buy_fee_krw,
                    sell_fee_krw=sell_fee_krw,
                    sell_tax_krw=sell_tax_krw,
                    net_pnl_krw=net_pnl_krw,
                    net_return_percent=net_return_percent,
                )
            )
            buy_lot["quantity"] = int(buy_lot["quantity"]) - matched
            remaining -= matched
            if int(buy_lot["quantity"]) <= 0:
                open_buys[ticker].popleft()

    return trades


def summarize_realized_performance(db_path: str, session_date: str | None = None) -> PerformanceSummary:
    trades = load_realized_trades(db_path, session_date=session_date)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if session_date:
        rows = cur.execute(
            """
            select ticker, side, quantity, price, source
            from fills
            where substr(filled_at, 1, 10) = ?
            order by filled_at, id
            """,
            (session_date,),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            select ticker, side, quantity, price, source
            from fills
            order by filled_at, id
            """
        ).fetchall()
    conn.close()

    open_buys: dict[str, deque[tuple[int, int]]] = defaultdict(deque)
    for row in rows:
        ticker = str(row["ticker"] or "")
        side = str(row["side"] or "").upper()
        source = str(row["source"] or "")
        if not should_include_in_fill_audit(source):
            continue
        quantity = int(row["quantity"] or 0)
        price = int(row["price"] or 0)
        if side == "BUY":
            open_buys[ticker].append((quantity, price))
        elif side == "SELL":
            remaining = quantity
            while remaining > 0 and open_buys[ticker]:
                buy_qty, buy_price = open_buys[ticker][0]
                matched = min(remaining, buy_qty)
                buy_qty -= matched
                remaining -= matched
                if buy_qty <= 0:
                    open_buys[ticker].popleft()
                else:
                    open_buys[ticker][0] = (buy_qty, buy_price)

    open_buy_count = 0
    open_buy_cost_krw = 0
    for lots in open_buys.values():
        for quantity, price in lots:
            open_buy_count += quantity
            open_buy_cost_krw += quantity * price

    gross_pnl_krw = sum(trade.pnl_krw for trade in trades)
    wins = sum(1 for trade in trades if trade.pnl_krw > 0)
    losses = sum(1 for trade in trades if trade.pnl_krw < 0)
    breakeven = sum(1 for trade in trades if trade.pnl_krw == 0)

    return PerformanceSummary(
        trade_count=len(trades),
        gross_pnl_krw=gross_pnl_krw,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        open_buy_count=open_buy_count,
        open_buy_cost_krw=open_buy_cost_krw,
        trades=trades,
    )


def summarize_trade_edge(db_path: str, session_date: str | None = None) -> TradeEdgeSummary:
    trades = load_realized_trades(db_path, session_date=session_date)
    wins = [trade for trade in trades if trade.net_pnl_krw > 0]
    losses = [trade for trade in trades if trade.net_pnl_krw < 0]
    breakeven = [trade for trade in trades if trade.net_pnl_krw == 0]

    trade_count = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    breakeven_count = len(breakeven)
    actual_win_rate_percent = (win_count / trade_count * 100) if trade_count > 0 else 0.0

    def _avg(values: list[float]) -> float:
        return (sum(values) / len(values)) if values else 0.0

    avg_gross_win_percent = _avg([trade.return_percent for trade in wins])
    avg_gross_loss_percent = _avg([trade.return_percent for trade in losses])
    avg_net_win_percent = _avg([trade.net_return_percent for trade in wins])
    avg_net_loss_percent = _avg([trade.net_return_percent for trade in losses])
    avg_gross_win_krw = _avg([float(trade.pnl_krw) for trade in wins])
    avg_gross_loss_krw = _avg([abs(float(trade.pnl_krw)) for trade in losses])
    avg_net_win_krw = _avg([trade.net_pnl_krw for trade in wins])
    avg_net_loss_krw = _avg([abs(trade.net_pnl_krw) for trade in losses])

    gross_payoff_ratio = (avg_gross_win_krw / avg_gross_loss_krw) if avg_gross_loss_krw > 0 else None
    net_payoff_ratio = (avg_net_win_krw / avg_net_loss_krw) if avg_net_loss_krw > 0 else None
    gross_required_win_rate_percent = (
        avg_gross_loss_krw / (avg_gross_win_krw + avg_gross_loss_krw) * 100
        if avg_gross_win_krw > 0 and avg_gross_loss_krw > 0
        else None
    )
    net_required_win_rate_percent = (
        avg_net_loss_krw / (avg_net_win_krw + avg_net_loss_krw) * 100
        if avg_net_win_krw > 0 and avg_net_loss_krw > 0
        else None
    )

    gross_pnl_krw = sum(trade.pnl_krw for trade in trades)
    net_pnl_krw = int(round(sum(trade.net_pnl_krw for trade in trades)))
    net_expectancy_krw = _avg([trade.net_pnl_krw for trade in trades])
    net_expectancy_percent = _avg([trade.net_return_percent for trade in trades])

    return TradeEdgeSummary(
        trade_count=trade_count,
        wins=win_count,
        losses=loss_count,
        breakeven=breakeven_count,
        actual_win_rate_percent=actual_win_rate_percent,
        gross_pnl_krw=gross_pnl_krw,
        net_pnl_krw=net_pnl_krw,
        avg_gross_win_percent=avg_gross_win_percent,
        avg_gross_loss_percent=avg_gross_loss_percent,
        avg_net_win_percent=avg_net_win_percent,
        avg_net_loss_percent=avg_net_loss_percent,
        avg_gross_win_krw=avg_gross_win_krw,
        avg_gross_loss_krw=avg_gross_loss_krw,
        avg_net_win_krw=avg_net_win_krw,
        avg_net_loss_krw=avg_net_loss_krw,
        gross_payoff_ratio=gross_payoff_ratio,
        net_payoff_ratio=net_payoff_ratio,
        gross_required_win_rate_percent=gross_required_win_rate_percent,
        net_required_win_rate_percent=net_required_win_rate_percent,
        net_expectancy_krw=net_expectancy_krw,
        net_expectancy_percent=net_expectancy_percent,
        trades=trades,
    )


def summarize_daily_revenue(
    db_path: str,
    session_date: str,
    starting_capital_krw: int,
) -> DailyRevenueSummary:
    realized_summary = summarize_realized_performance(db_path, session_date=session_date)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ticker, side, quantity, price, filled_at, raw_json, source
        FROM fills
        WHERE substr(filled_at, 1, 10) = ?
        ORDER BY filled_at ASC, id ASC
        """,
        (session_date,),
    ).fetchall()
    conn.close()

    total_buy_amount = 0
    total_sell_amount = 0
    total_fee = 0.0
    total_tax = 0.0
    traded_tickers: list[str] = []
    seen_tickers: set[str] = set()

    for row in rows:
        ticker = str(row["ticker"] or "").strip()
        side = str(row["side"] or "").upper()
        source = str(row["source"] or "")
        if not should_include_in_fill_audit(source):
            continue
        quantity = int(row["quantity"] or 0)
        price = int(row["price"] or 0)
        amount = quantity * price
        raw_json = str(row["raw_json"] or "")
        try:
            raw = json.loads(raw_json) if raw_json else None
        except json.JSONDecodeError:
            raw = {"raw_json": raw_json}
        fill = Fill(
            order_id="",
            ticker=ticker,
            quantity=quantity,
            price=price,
            raw=raw,
        )
        fee, tax = estimate_fill_costs(fill, side)
        total_fee += fee
        total_tax += tax

        if side == "BUY":
            total_buy_amount += amount
        elif side == "SELL":
            total_sell_amount += amount

        if ticker and ticker not in seen_tickers:
            seen_tickers.add(ticker)
            traded_tickers.append(ticker)

    total_profit = int(round(realized_summary.gross_pnl_krw - total_fee - total_tax))
    total_return_percent = (total_profit / total_buy_amount * 100) if total_buy_amount > 0 else 0.0
    total_return_percent_on_starting_capital = (
        total_profit / starting_capital_krw * 100 if starting_capital_krw > 0 else 0.0
    )

    return DailyRevenueSummary(
        session_date=session_date,
        starting_capital_krw=int(starting_capital_krw),
        total_profit_krw=total_profit,
        total_fee_krw=int(round(total_fee)),
        total_tax_krw=int(round(total_tax)),
        total_buy_amount_krw=total_buy_amount,
        total_sell_amount_krw=total_sell_amount,
        total_return_percent=total_return_percent,
        total_return_percent_on_starting_capital=total_return_percent_on_starting_capital,
        traded_tickers=traded_tickers,
    )
