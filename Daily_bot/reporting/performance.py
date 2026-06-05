from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass


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


def load_realized_trades(db_path: str, session_date: str | None = None) -> list[RealizedTrade]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if session_date:
        rows = cur.execute(
            """
            select ticker, side, quantity, price, filled_at, created_at
            from fills
            where date(created_at,'localtime') = ?
            order by created_at, id
            """,
            (session_date,),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            select ticker, side, quantity, price, filled_at, created_at
            from fills
            order by created_at, id
            """
        ).fetchall()

    conn.close()

    open_buys: dict[str, deque[dict[str, int | str]]] = defaultdict(deque)
    trades: list[RealizedTrade] = []

    for row in rows:
        side = str(row["side"] or "").upper()
        ticker = str(row["ticker"] or "")
        quantity = int(row["quantity"] or 0)
        price = int(row["price"] or 0)
        filled_at = str(row["filled_at"] or row["created_at"] or "")

        if side == "BUY":
            open_buys[ticker].append(
                {
                    "quantity": quantity,
                    "price": price,
                    "filled_at": filled_at,
                }
            )
            continue

        if side != "SELL":
            continue

        remaining = quantity
        while remaining > 0 and open_buys[ticker]:
            buy_lot = open_buys[ticker][0]
            matched = min(remaining, int(buy_lot["quantity"]))
            buy_price = int(buy_lot["price"])
            pnl_krw = (price - buy_price) * matched
            return_percent = ((price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
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
            select ticker, side, quantity, price
            from fills
            where date(created_at,'localtime') = ?
            order by created_at, id
            """,
            (session_date,),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            select ticker, side, quantity, price
            from fills
            order by created_at, id
            """
        ).fetchall()
    conn.close()

    open_buys: dict[str, deque[tuple[int, int]]] = defaultdict(deque)
    for row in rows:
        ticker = str(row["ticker"] or "")
        side = str(row["side"] or "").upper()
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
