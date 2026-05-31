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
"""


class Recorder:
    def __init__(self, path: str | Path = "bot.sqlite3", log_dir: str | Path = "Daily_bot/logs"):
        self.path = Path(path)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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
