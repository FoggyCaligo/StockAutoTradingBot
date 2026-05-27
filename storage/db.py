from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from models import Candidate, HogaSnapshot, OrderResult


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
"""


class Recorder:
    def __init__(self, path: str | Path = "bot.sqlite3"):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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
                json.dumps(order.raw or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
