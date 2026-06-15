from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from Daily_bot.models import Candidate, Fill, HogaSnapshot, OrderResult
from Daily_bot.reporting.performance import summarize_daily_revenue
from Daily_bot.storage.audit_csv import append_fill_audit_csv, rewrite_fill_audit_csv, should_include_in_fill_audit


SCHEMA = """
CREATE TABLE IF NOT EXISTS hoga_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    scan_cycle_at TEXT,
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
    scan_cycle_at TEXT,
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
    scan_cycle_at TEXT,
    selected INTEGER DEFAULT 0,
    reason TEXT,
    price INTEGER,
    current_price INTEGER,
    best_bid INTEGER,
    best_ask INTEGER,
    expect_price INTEGER,
    expect_revenue_percent REAL,
    spread_percent REAL,
    kospi_change_percent REAL,
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
    kospi_change_percent REAL,
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
    "kospi_change_percent": "REAL",
}

TABLE_EXTRA_COLUMNS = {
    "hoga_snapshots": {
        "scan_cycle_at": "TEXT",
    },
    "signals": {
        "scan_cycle_at": "TEXT",
    },
    "market_traces": {
        "scan_cycle_at": "TEXT",
        "kospi_change_percent": "REAL",
    },
}

DAILY_REV_FIELDNAMES = [
    "session_date",
    "starting_capital_krw",
    "total_profit_krw",
    "total_fee_krw",
    "total_tax_krw",
    "total_buy_amount_krw",
    "total_sell_amount_krw",
    "total_return_percent",
    "traded_tickers",
]


class Recorder:
    def __init__(self, path: str | Path = "bot.sqlite3", log_dir: str | Path | None = None):
        self.path = Path(path)
        self.log_dir = Path(log_dir) if log_dir is not None else self.path.parent / "logs"
        self.audit_fill_csv_path = self.log_dir / "trade_fills_audit.csv"
        self.daily_revenue_csv_path = self.log_dir / "daily_rev.csv"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if log_dir is None:
            self._migrate_legacy_logs()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_table_columns()
        self._ensure_account_trace_columns()
        self.conn.commit()

    def _ensure_table_columns(self) -> None:
        for table_name, extra_columns in TABLE_EXTRA_COLUMNS.items():
            existing_columns = {
                row["name"] for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            for column_name, column_type in extra_columns.items():
                if column_name not in existing_columns:
                    self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _ensure_account_trace_columns(self) -> None:
        existing_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(account_traces)").fetchall()
        }
        for column_name, column_type in ACCOUNT_TRACE_EXTRA_COLUMNS.items():
            if column_name not in existing_columns:
                self.conn.execute(f"ALTER TABLE account_traces ADD COLUMN {column_name} {column_type}")

    def _migrate_legacy_logs(self) -> None:
        legacy_dir = self.path.parent / "Daily_bot" / "logs"
        if legacy_dir.resolve() == self.log_dir.resolve() or not legacy_dir.exists():
            return

        for legacy_path in legacy_dir.iterdir():
            if not legacy_path.is_file():
                continue
            target_path = self.log_dir / legacy_path.name
            if not target_path.exists():
                legacy_path.replace(target_path)
                continue
            if legacy_path.suffix.lower() != ".csv":
                target_path = self.log_dir / f"legacy_{legacy_path.name}"
                legacy_path.replace(target_path)
                continue
            self._merge_csv_file(legacy_path, target_path)
            legacy_path.unlink()

        try:
            legacy_dir.rmdir()
            legacy_parent = legacy_dir.parent
            if legacy_parent != self.path.parent:
                legacy_parent.rmdir()
        except OSError:
            pass

    def _merge_csv_file(self, source_path: Path, target_path: Path) -> None:
        source_text = source_path.read_text(encoding="utf-8-sig")
        if not source_text.strip():
            return

        source_lines = source_text.splitlines()
        if not target_path.exists() or target_path.stat().st_size == 0:
            target_path.write_text(source_text, encoding="utf-8-sig", newline="")
            return

        payload_lines = source_lines[1:] if len(source_lines) > 1 else []
        if not payload_lines:
            return

        with target_path.open("a", encoding="utf-8-sig", newline="") as fp:
            if target_path.stat().st_size > 0:
                fp.write("\n")
            fp.write("\n".join(payload_lines))

    def _daily_csv_path(self, prefix: str) -> Path:
        from datetime import datetime

        return self.log_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d')}.csv"

    def _append_csv_row(self, path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 0:
            with path.open("r", newline="", encoding="utf-8-sig") as fp:
                reader = csv.reader(fp)
                existing_header = next(reader, [])
            if existing_header != fieldnames:
                self._rewrite_csv_with_new_header(path, fieldnames)
        should_write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8-sig") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            if should_write_header:
                writer.writeheader()
            writer.writerow({field: row.get(field, "") for field in fieldnames})

    def _rewrite_csv_with_new_header(self, path: Path, fieldnames: list[str]) -> None:
        if not path.exists() or path.stat().st_size == 0:
            return
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            rows = list(csv.DictReader(fp))
        with path.open("w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})

    def _upsert_csv_row(self, path: Path, fieldnames: list[str], key_field: str, row: dict[str, Any]) -> None:
        existing_rows: list[dict[str, Any]] = []
        if path.exists() and path.stat().st_size > 0:
            with path.open("r", newline="", encoding="utf-8-sig") as fp:
                existing_rows = list(csv.DictReader(fp))

        key_value = str(row.get(key_field, ""))
        updated = False
        for existing_row in existing_rows:
            if str(existing_row.get(key_field, "")) == key_value:
                for field in fieldnames:
                    existing_row[field] = row.get(field, "")
                updated = True
                break
        if not updated:
            existing_rows.append({field: row.get(field, "") for field in fieldnames})

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            for existing_row in existing_rows:
                writer.writerow({field: existing_row.get(field, "") for field in fieldnames})

    def _latest_account_trace(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT cash, account_value, adjusted_account_value, adjusted_pnl, loss_percent, kospi_change_percent
            FROM account_traces
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row is not None else None

    def save_snapshot(
        self,
        candidate: Candidate,
        snapshot: HogaSnapshot,
        scan_cycle_at: datetime | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO hoga_snapshots
            (ticker, captured_at, scan_cycle_at, current_price, expect_price, expect_revenue_percent, spread_percent, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.ticker,
                snapshot.captured_at.isoformat(),
                scan_cycle_at.isoformat() if scan_cycle_at is not None else None,
                snapshot.current_price,
                candidate.expect_price,
                candidate.expect_revenue_percent,
                candidate.spread_percent,
                json.dumps(snapshot.raw or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def save_signal(
        self,
        candidate: Candidate,
        selected: bool = False,
        scan_cycle_at: datetime | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO signals
            (ticker, created_at, scan_cycle_at, price, expect_price, expect_revenue_percent, spread_percent, selected)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.ticker,
                scan_cycle_at.isoformat() if scan_cycle_at is not None else None,
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
        scan_cycle_at: datetime | None = None,
        kospi_change_percent: float | None = None,
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
            "scan_cycle_at": scan_cycle_at.isoformat() if scan_cycle_at is not None else None,
            "selected": 1 if selected else 0,
            "reason": reason,
            "price": candidate.price,
            "current_price": snapshot.current_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "expect_price": candidate.expect_price,
            "expect_revenue_percent": candidate.expect_revenue_percent,
            "spread_percent": candidate.spread_percent,
            "kospi_change_percent": kospi_change_percent,
            "raw_json": raw_json,
        }
        self.conn.execute(
            """
            INSERT INTO market_traces
            (session_date, phase, ticker, scan_cycle_at, selected, reason, price, current_price, best_bid, best_ask,
             expect_price, expect_revenue_percent, spread_percent, kospi_change_percent, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["session_date"],
                row["phase"],
                row["ticker"],
                row["scan_cycle_at"],
                row["selected"],
                row["reason"],
                row["price"],
                row["current_price"],
                row["best_bid"],
                row["best_ask"],
                row["expect_price"],
                row["expect_revenue_percent"],
                row["spread_percent"],
                row["kospi_change_percent"],
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
                "scan_cycle_at",
                "selected",
                "reason",
                "price",
                "current_price",
                "best_bid",
                "best_ask",
                "expect_price",
                "expect_revenue_percent",
                "spread_percent",
                "kospi_change_percent",
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
        kospi_change_percent: float | None = None,
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
            "kospi_change_percent": kospi_change_percent,
            "positions_json": positions_json,
            "open_orders_json": open_orders_json,
        }
        self.conn.execute(
            """
            INSERT INTO account_traces
            (session_date, phase, cash, account_value, external_cash_flow, adjusted_account_value,
             adjusted_pnl, loss_percent, kospi_change_percent, positions_json, open_orders_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                row["kospi_change_percent"],
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
                "kospi_change_percent",
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
        self._persist_fill(fill, side=side, source=source, replace_existing=False)

    def replace_fill(self, fill: Fill, side: str, source: str = "broker") -> None:
        self._persist_fill(fill, side=side, source=source, replace_existing=True)

    def _persist_fill(
        self,
        fill: Fill,
        side: str,
        source: str,
        replace_existing: bool,
    ) -> None:
        raw_json = json.dumps(fill.raw or {}, ensure_ascii=False)
        filled_at = fill.filled_at.isoformat()
        side_upper = str(side or "").strip().upper()
        if replace_existing:
            self.conn.execute(
                """
                DELETE FROM fills
                WHERE broker_order_id = ?
                  AND side = ?
                """,
                (fill.order_id, side_upper),
            )
        self.conn.execute(
            """
            INSERT INTO fills
            (broker_order_id, ticker, side, quantity, price, filled_at, source, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.order_id,
                fill.ticker,
                side_upper,
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
                "side": side_upper,
                "quantity": fill.quantity,
                "price": fill.price,
                "filled_at": filled_at,
                "source": source,
                "raw_json": raw_json,
            },
        )
        if should_include_in_fill_audit(source):
            try:
                append_fill_audit_csv(
                    self.audit_fill_csv_path,
                    fill,
                    side=side_upper,
                    source=source,
                    account_snapshot=self._latest_account_trace(),
                )
            except Exception as exc:
                print(f"Failed to append fill audit CSV for {fill.ticker}: {exc}")
        print(
            f"FILL {side_upper} {fill.ticker} qty={fill.quantity} price={fill.price} "
            f"filled_at={filled_at} source={source} order_id={fill.order_id}"
        )

    def get_session_fills(self, session_date: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT broker_order_id, ticker, side, quantity, price, filled_at, source, raw_json, created_at
            FROM fills
            WHERE substr(filled_at, 1, 10) = ?
            ORDER BY filled_at ASC, id ASC
            """,
            (session_date,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_fill_index(self, session_date: str) -> dict[tuple[str, str], dict[str, Any]]:
        index: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.get_session_fills(session_date):
            key = (str(row.get("broker_order_id") or "").strip(), str(row.get("side") or "").strip().upper())
            if key[0] and key[1]:
                index[key] = row
        return index

    def has_recorded_sell_fill_after(
        self,
        ticker: str,
        created_at: str | None,
        exclude_order_id: str,
        minimum_quantity: int = 1,
    ) -> bool:
        normalized_created_at = str(created_at or "").strip().replace(" ", "T")
        rows = self.conn.execute(
            """
            SELECT quantity, filled_at
            FROM fills
            WHERE side = 'SELL'
              AND ticker = ?
              AND broker_order_id != ?
              AND source != 'sell_reconciliation'
            ORDER BY filled_at ASC, id ASC
            """,
            (ticker, exclude_order_id),
        ).fetchall()
        for row in rows:
            filled_at = str(row["filled_at"] or "").strip()
            if normalized_created_at and filled_at and filled_at < normalized_created_at:
                continue
            if int(row["quantity"] or 0) >= max(1, minimum_quantity):
                return True
        return False

    def rebuild_session_fill_exports(self, session_date: str) -> None:
        fill_rows = self.get_session_fills(session_date)
        fills_csv_path = self.log_dir / f"fills_{session_date.replace('-', '')}.csv"
        if fills_csv_path.exists():
            fills_csv_path.unlink()

        fill_fieldnames = ["broker_order_id", "ticker", "side", "quantity", "price", "filled_at", "source", "raw_json"]
        for row in fill_rows:
            self._append_csv_row(
                fills_csv_path,
                fill_fieldnames,
                {
                    "broker_order_id": row["broker_order_id"],
                    "ticker": row["ticker"],
                    "side": row["side"],
                    "quantity": row["quantity"],
                    "price": row["price"],
                    "filled_at": row["filled_at"],
                    "source": row["source"],
                    "raw_json": row["raw_json"],
                },
            )

        existing_snapshot_map = self._read_audit_snapshot_map()
        audit_rows = self.conn.execute(
            """
            SELECT broker_order_id, ticker, side, quantity, price, filled_at, source, raw_json
            FROM fills
            ORDER BY filled_at ASC, id ASC
            """
        ).fetchall()
        audit_entries: list[tuple[Fill, str, str]] = []
        for row in audit_rows:
            raw_json = row["raw_json"]
            try:
                raw = json.loads(raw_json) if raw_json else None
            except json.JSONDecodeError:
                raw = {"raw_json": raw_json or ""}
            source = str(row["source"] or "")
            if not should_include_in_fill_audit(source):
                continue
            audit_entries.append(
                (
                    Fill(
                        order_id=str(row["broker_order_id"] or ""),
                        ticker=str(row["ticker"] or ""),
                        quantity=int(row["quantity"] or 0),
                        price=int(row["price"] or 0),
                        filled_at=datetime.fromisoformat(str(row["filled_at"])),
                        raw=raw,
                    ),
                    str(row["side"] or "").upper(),
                    source,
                )
            )

        rewrite_fill_audit_csv(
            self.audit_fill_csv_path,
            audit_entries,
            account_snapshots_by_order_id=existing_snapshot_map,
        )

    def _read_audit_snapshot_map(self) -> dict[str, dict[str, Any]]:
        if not self.audit_fill_csv_path.exists() or self.audit_fill_csv_path.stat().st_size == 0:
            return {}
        snapshot_map: dict[str, dict[str, Any]] = {}
        with self.audit_fill_csv_path.open("r", newline="", encoding="utf-8-sig") as fp:
            for row in csv.DictReader(fp):
                order_id = str(row.get("broker_order_id") or "").strip()
                if not order_id:
                    continue
                snapshot_map[order_id] = {
                    "cash": row.get("cash", ""),
                    "account_value": row.get("account_value", ""),
                    "adjusted_account_value": row.get("adjusted_account_value", ""),
                    "adjusted_pnl": row.get("adjusted_pnl", ""),
                    "loss_percent": row.get("loss_percent", ""),
                    "kospi_change_percent": row.get("kospi_change_percent", ""),
                }
        return snapshot_map

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

    def purge_superseded_sell_reconciliation_fills(self, session_date: str | None = None) -> int:
        params: list[Any] = []
        session_filter = ""
        if session_date:
            session_filter = "AND substr(sr.filled_at, 1, 10) = ?"
            params.append(session_date)

        rows = self.conn.execute(
            f"""
            SELECT
                sr.id,
                sr.broker_order_id,
                sr.ticker,
                sr.quantity,
                COALESCE(o.created_at, sr.filled_at) AS order_created_at
            FROM fills sr
            LEFT JOIN orders o
              ON o.broker_order_id = sr.broker_order_id
             AND o.side = 'SELL'
            WHERE sr.side = 'SELL'
              AND sr.source = 'sell_reconciliation'
              {session_filter}
            ORDER BY sr.id ASC
            """,
            params,
        ).fetchall()

        stale_ids: list[int] = []
        for row in rows:
            if self.has_recorded_sell_fill_after(
                ticker=str(row["ticker"] or ""),
                created_at=str(row["order_created_at"] or ""),
                exclude_order_id=str(row["broker_order_id"] or ""),
                minimum_quantity=int(row["quantity"] or 0),
            ):
                stale_ids.append(int(row["id"]))

        if not stale_ids:
            return 0

        placeholders = ",".join("?" for _ in stale_ids)
        self.conn.execute(f"DELETE FROM fills WHERE id IN ({placeholders})", stale_ids)
        self.conn.commit()
        return len(stale_ids)

    def write_daily_revenue_summary(self, session_date: str, starting_capital_krw: int) -> None:
        summary = summarize_daily_revenue(str(self.path), session_date, starting_capital_krw)
        row = {
            "session_date": summary.session_date,
            "starting_capital_krw": summary.starting_capital_krw,
            "total_profit_krw": summary.total_profit_krw,
            "total_fee_krw": summary.total_fee_krw,
            "total_tax_krw": summary.total_tax_krw,
            "total_buy_amount_krw": summary.total_buy_amount_krw,
            "total_sell_amount_krw": summary.total_sell_amount_krw,
            "total_return_percent": f"{summary.total_return_percent:.4f}",
            "traded_tickers": ",".join(summary.traded_tickers),
        }
        self._upsert_csv_row(self.daily_revenue_csv_path, DAILY_REV_FIELDNAMES, "session_date", row)
