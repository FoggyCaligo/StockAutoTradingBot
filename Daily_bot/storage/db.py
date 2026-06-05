from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from Daily_bot.models import Candidate, Fill, HogaSnapshot, OrderResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS hoga_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    current_price INTEGER,
    expect_price INTEGER,
    expect_revenue_percent REAL,
    spread_percent REAL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    created_at TEXT NOT NULL,
    price INTEGER,
    expect_price INTEGER,
    expect_revenue_percent REAL,
    spread_percent REAL,
    selected INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_order_id TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER,
    price INTEGER,
    status TEXT,
    raw_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_order_id TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price INTEGER NOT NULL,
    filled_at TEXT NOT NULL,
    source TEXT,
    raw_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL,
    phase TEXT NOT NULL,
    ticker TEXT NOT NULL,
    selected INTEGER DEFAULT 0,
    reason TEXT,
    price INTEGER,
    current_price INTEGER,
    best_bid INTEGER,
    best_ask INTEGER,
    expect_price INTEGER,
    expect_revenue_percent REAL,
    spread_percent REAL,
    raw_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL,
    phase TEXT NOT NULL,
    cash INTEGER,
    account_value INTEGER,
    external_cash_flow INTEGER DEFAULT 0,
    adjusted_account_value INTEGER,
    adjusted_pnl INTEGER,
    loss_percent REAL,
    positions_json TEXT,
    open_orders_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


ACCOUNT_TRACE_EXTRA_COLUMNS = {
    "external_cash_flow": "INTEGER DEFAULT 0",
    "adjusted_account_value": "INTEGER",
    "adjusted_pnl": "INTEGER",
    "loss_percent": "REAL",
}


class Recorder:
    def __init__(self, path: str | Path = "bot.sqlite3", log_dir: str | Path = "Daily_bot/logs"):
        self.path = Path(path)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_account_trace_columns()
        self.conn.commit()

    def _ensure_account_trace_columns(self) -> None:
        existing_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(account_traces)").fetchall()
        }
        for column_name, column_type in ACCOUNT_TRACE_EXTRA_COLUMNS.items():
            if column_name not in existing_columns:
                self.conn.execute(f"ALTER TABLE account_traces ADD COLUMN {column_name} {column_type}")

    def _daily_csv_path(self, prefix: str) -> Path:
        from datetime import datetime

        return self.log_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d')}.csv"

    def _append_csv_row(self, path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        should_write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8-sig") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            if should_write_header:
                writer.writeheader()
            writer.writerow({field: row.get(field, "") for field in fieldnames})

    def save_snapshot(self, candidate: Candidate, snapshot: HogaSnapshot) -> None:
        self.conn.execute(
            """
            INSERT INTO hoga_snapshots
            (ticker, captured_at, current_price, expect_price, expect_revenue_percent, spread_percent, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.ticker,
                snapshot.captured_at.isoformat(),
                snapshot.current_price,
                candidate.expect_price,
                candidate.expect_revenue_percent,
                candidate.spread_percent,
                json.dumps(snapshot.raw or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def save_signal(self, candidate: Candidate, selected: bool = False) -> None:
        self.conn.execute(
            """
            INSERT INTO signals
            (ticker, created_at, price, expect_price, expect_revenue_percent, spread_percent, selected)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
            """,
            (
                candidate.ticker,
                candidate.price,
                candidate.expect_price,
                candidate.expect_revenue_percent,
                candidate.spread_percent,
                1 if selected else 0,
            ),
        )
        self.conn.commit()

    def save_market_trace(
        self,
        candidate: Candidate,
        snapshot: HogaSnapshot,
        phase: str,
        selected: bool = False,
        reason: str = "",
    ) -> None:
        from datetime import datetime

        session_date = datetime.now().strftime("%Y-%m-%d")
        raw_json = json.dumps(snapshot.raw or {}, ensure_ascii=False)
        best_bid = snapshot.bids[0].price if snapshot.bids else 0
        best_ask = snapshot.asks[0].price if snapshot.asks else 0
        row = {
            "session_date": session_date,
            "phase": phase,
            "ticker": candidate.ticker,
            "selected": 1 if selected else 0,
            "reason": reason,
            "price": candidate.price,
            "current_price": snapshot.current_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "expect_price": candidate.expect_price,
            "expect_revenue_percent": candidate.expect_revenue_percent,
            "spread_percent": candidate.spread_percent,
            "raw_json": raw_json,
        }
        self.conn.execute(
            """
            INSERT INTO market_traces
            (session_date, phase, ticker, selected, reason, price, current_price, best_bid, best_ask,
             expect_price, expect_revenue_percent, spread_percent, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["session_date"],
                row["phase"],
                row["ticker"],
                row["selected"],
                row["reason"],
                row["price"],
                row["current_price"],
                row["best_bid"],
                row["best_ask"],
                row["expect_price"],
                row["expect_revenue_percent"],
                row["spread_percent"],
                row["raw_json"],
            ),
        )
        self.conn.commit()
        self._append_csv_row(
            self._daily_csv_path("market_traces"),
            [
                "session_date",
                "phase",
                "ticker",
                "selected",
                "reason",
                "price",
                "current_price",
                "best_bid",
                "best_ask",
                "expect_price",
                "expect_revenue_percent",
                "spread_percent",
                "raw_json",
            ],
            row,
        )

    def save_account_trace(
        self,
        phase: str,
        cash: int,
        account_value: int,
        positions: list[Any],
        open_orders: list[dict[str, Any]],
        external_cash_flow: int = 0,
        adjusted_account_value: int | None = None,
        adjusted_pnl: int | None = None,
        loss_percent: float | None = None,
    ) -> None:
        from datetime import datetime

        session_date = datetime.now().strftime("%Y-%m-%d")
        positions_json = json.dumps([getattr(position, "__dict__", position) for position in positions], ensure_ascii=False, default=str)
        open_orders_json = json.dumps(open_orders, ensure_ascii=False, default=str)
        row = {
            "session_date": session_date,
            "phase": phase,
            "cash": cash,
            "account_value": account_value,
            "external_cash_flow": external_cash_flow,
            "adjusted_account_value": adjusted_account_value if adjusted_account_value is not None else account_value - external_cash_flow,
            "adjusted_pnl": adjusted_pnl,
            "loss_percent": loss_percent,
            "positions_json": positions_json,
            "open_orders_json": open_orders_json,
        }
        self.conn.execute(
            """
            INSERT INTO account_traces
            (session_date, phase, cash, account_value, external_cash_flow, adjusted_account_value,
             adjusted_pnl, loss_percent, positions_json, open_orders_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["session_date"],
                row["phase"],
                row["cash"],
                row["account_value"],
                row["external_cash_flow"],
                row["adjusted_account_value"],
                row["adjusted_pnl"],
                row["loss_percent"],
                row["positions_json"],
                row["open_orders_json"],
            ),
        )
        self.conn.commit()
        self._append_csv_row(
            self._daily_csv_path("account_traces"),
            [
                "session_date",
                "phase",
                "cash",
                "account_value",
                "external_cash_flow",
                "adjusted_account_value",
                "adjusted_pnl",
                "loss_percent",
                "positions_json",
                "open_orders_json",
            ],
            row,
        )

    def save_order(self, order: OrderResult) -> None:
        raw_json = json.dumps(order.raw or {}, ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO orders
            (broker_order_id, ticker, side, quantity, price, status, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.ticker,
                order.side,
                order.quantity,
                order.price,
                order.status,
                raw_json,
            ),
        )
        self.conn.commit()
        self._append_csv_row(
            self._daily_csv_path("orders"),
            ["broker_order_id", "ticker", "side", "quantity", "price", "status", "raw_json"],
            {
                "broker_order_id": order.order_id,
                "ticker": order.ticker,
                "side": order.side,
                "quantity": order.quantity,
                "price": order.price,
                "status": order.status,
                "raw_json": raw_json,
            },
        )

    def save_fill(self, fill: Fill, side: str, source: str = "broker") -> None:
        raw_json = json.dumps(fill.raw or {}, ensure_ascii=False)
        filled_at = fill.filled_at.isoformat()
        self.conn.execute(
            """
            INSERT INTO fills
            (broker_order_id, ticker, side, quantity, price, filled_at, source, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.order_id,
                fill.ticker,
                side,
                fill.quantity,
                fill.price,
                filled_at,
                source,
                raw_json,
            ),
        )
        self.conn.commit()
        self._append_csv_row(
            self._daily_csv_path("fills"),
            ["broker_order_id", "ticker", "side", "quantity", "price", "filled_at", "source", "raw_json"],
            {
                "broker_order_id": fill.order_id,
                "ticker": fill.ticker,
                "side": side,
                "quantity": fill.quantity,
                "price": fill.price,
                "filled_at": filled_at,
                "source": source,
                "raw_json": raw_json,
            },
        )
        print(
            f"FILL {side} {fill.ticker} qty={fill.quantity} price={fill.price} "
            f"filled_at={filled_at} source={source} order_id={fill.order_id}"
        )

    def get_orders_needing_fill_poll(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
                o.broker_order_id,
                o.ticker,
                o.side,
                o.quantity,
                o.price,
                o.status,
                o.created_at,
                COALESCE(SUM(f.quantity), 0) AS recorded_fill_quantity
            FROM orders o
            LEFT JOIN fills f ON f.broker_order_id = o.broker_order_id
            WHERE o.broker_order_id IS NOT NULL
              AND o.broker_order_id != ''
            GROUP BY o.id
            HAVING COALESCE(SUM(f.quantity), 0) < COALESCE(o.quantity, 0)
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
