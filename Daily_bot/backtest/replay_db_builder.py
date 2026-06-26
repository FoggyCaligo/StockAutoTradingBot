from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from Daily_bot.storage.db import SCHEMA


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def has_replay_source_data(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size <= 0:
        return False
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'market_traces'
            """
        ).fetchone()
        if row is None:
            conn.close()
            return False
        count_row = conn.execute("SELECT COUNT(*) FROM market_traces").fetchone()
        conn.close()
        return bool(count_row and int(count_row[0] or 0) > 0)
    except sqlite3.DatabaseError:
        return False


def _normalize_session_date(session_date_text: str) -> str:
    text = str(session_date_text or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _parse_market_trace_created_at(row: dict[str, str], session_date: str, sequence: int) -> str:
    created_at = str(row.get("created_at") or "").strip()
    if created_at:
        return created_at

    raw_json = str(row.get("raw_json") or "").strip()
    if raw_json:
        try:
            raw = json.loads(raw_json)
        except json.JSONDecodeError:
            raw = {}
        time_text = str(
            raw.get("bid_req_base_tm")
            or raw.get("captured_at")
            or raw.get("capture_time")
            or ""
        ).strip()
        if len(time_text) == 6 and time_text.isdigit():
            return f"{session_date} {time_text[:2]}:{time_text[2:4]}:{time_text[4:6]}"
    return f"{session_date} 09:00:{sequence % 60:02d}"


def _parse_account_trace_created_at(row: dict[str, str], session_date: str, sequence: int) -> str:
    created_at = str(row.get("created_at") or "").strip()
    if created_at:
        return created_at
    return f"{session_date} 08:{(sequence // 60) % 60:02d}:{sequence % 60:02d}"


def _safe_session_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _resolve_prev_close_price_overrides(market_files: list[Path]) -> dict[tuple[str, str], int]:
    requirements: dict[str, set[str]] = {}
    for path in market_files:
        session_suffix = path.stem.split("_")[-1]
        default_session_date = _normalize_session_date(session_suffix)
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                session_date = _normalize_session_date(row.get("session_date") or default_session_date)
                ticker = str(row.get("ticker") or "").strip()
                current_price = _to_int(row.get("current_price"))
                prev_close_price = _to_int(row.get("prev_close_price"))
                if not ticker or current_price <= 0 or prev_close_price > 0:
                    continue
                requirements.setdefault(ticker, set()).add(session_date)

    if not requirements:
        return {}

    try:
        import FinanceDataReader as fdr  # type: ignore[import]
        import pandas as pd  # type: ignore[import]
    except Exception:
        return {}

    overrides: dict[tuple[str, str], int] = {}
    for ticker, session_dates in sorted(requirements.items()):
        parsed_dates = sorted(filter(None, (_safe_session_date(session_date) for session_date in session_dates)))
        if not parsed_dates:
            continue
        start = parsed_dates[0] - timedelta(days=14)
        end = parsed_dates[-1] + timedelta(days=1)
        try:
            df = fdr.DataReader(ticker, start=start, end=end)
        except Exception:
            continue
        if "Close" not in df.columns:
            continue
        close_series = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if close_series.empty:
            continue
        close_series.index = pd.to_datetime(close_series.index).tz_localize(None)
        for session_date in sorted(session_dates):
            session_day = _safe_session_date(session_date)
            if session_day is None:
                continue
            prior = close_series[close_series.index.date < session_day]
            if prior.empty:
                continue
            overrides[(session_date, ticker)] = _to_int(prior.iloc[-1])
    return overrides


def build_replay_db_from_logs(logs_dir: Path, out_db_path: Path) -> Path:
    market_files = sorted(logs_dir.glob("market_traces_*.csv"))
    if not market_files:
        raise FileNotFoundError(f"No market trace CSV files found under: {logs_dir}")

    out_db_path.parent.mkdir(parents=True, exist_ok=True)
    if out_db_path.exists():
        out_db_path.unlink()

    conn = sqlite3.connect(out_db_path)
    conn.executescript(SCHEMA)
    prev_close_overrides = _resolve_prev_close_price_overrides(market_files)

    for path in market_files:
        session_suffix = path.stem.split("_")[-1]
        default_session_date = _normalize_session_date(session_suffix)
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            for sequence, row in enumerate(reader):
                session_date = _normalize_session_date(row.get("session_date") or default_session_date)
                ticker = str(row.get("ticker") or "").strip()
                prev_close_price = _to_int(row.get("prev_close_price"))
                if prev_close_price <= 0:
                    prev_close_price = _to_int(prev_close_overrides.get((session_date, ticker)))
                current_price = _to_int(row.get("current_price"))
                prev_day_change_percent = _to_float(row.get("prev_day_change_percent"))
                if prev_close_price > 0 and current_price > 0:
                    prev_day_change_percent = ((current_price - prev_close_price) / prev_close_price) * 100
                conn.execute(
                    """
                    INSERT INTO market_traces (
                        session_date,
                        phase,
                        ticker,
                        selected,
                        reason,
                        price,
                        prev_close_price,
                        current_price,
                        best_bid,
                        best_ask,
                        expect_price,
                        expect_revenue_percent,
                        spread_percent,
                        ask_depth_5_amount_krw,
                        prev_day_change_percent,
                        raw_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_date,
                        row.get("phase") or "",
                        ticker,
                        _to_int(row.get("selected")),
                        row.get("reason") or "",
                        _to_int(row.get("price")),
                        prev_close_price,
                        current_price,
                        _to_int(row.get("best_bid")),
                        _to_int(row.get("best_ask")),
                        _to_int(row.get("expect_price")),
                        _to_float(row.get("expect_revenue_percent")),
                        _to_float(row.get("spread_percent")),
                        _to_int(row.get("ask_depth_5_amount_krw")),
                        prev_day_change_percent,
                        row.get("raw_json") or "{}",
                        _parse_market_trace_created_at(row, session_date, sequence),
                    ),
                )

    for path in sorted(logs_dir.glob("account_traces_*.csv")):
        session_suffix = path.stem.split("_")[-1]
        default_session_date = _normalize_session_date(session_suffix)
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            for sequence, row in enumerate(reader):
                session_date = _normalize_session_date(row.get("session_date") or default_session_date)
                conn.execute(
                    """
                    INSERT INTO account_traces (
                        session_date,
                        phase,
                        cash,
                        account_value,
                        external_cash_flow,
                        adjusted_account_value,
                        adjusted_pnl,
                        loss_percent,
                        positions_json,
                        open_orders_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_date,
                        row.get("phase") or "",
                        _to_int(row.get("cash")),
                        _to_int(row.get("account_value")),
                        _to_int(row.get("external_cash_flow")),
                        _to_int(row.get("adjusted_account_value")),
                        _to_int(row.get("adjusted_pnl")),
                        _to_float(row.get("loss_percent")),
                        row.get("positions_json") or "[]",
                        row.get("open_orders_json") or "[]",
                        _parse_account_trace_created_at(row, session_date, sequence),
                    ),
                )

    conn.commit()
    conn.close()
    return out_db_path


def resolve_replay_db_path(db_path: Path, logs_dir: Path | None = None) -> Path:
    if has_replay_source_data(db_path):
        return db_path

    resolved_logs_dir = Path(logs_dir) if logs_dir is not None else db_path.parent / "logs"
    cache_db_path = resolved_logs_dir.parent / "backtest" / "cache" / f"{db_path.stem}_replay_from_logs.sqlite3"
    return build_replay_db_from_logs(resolved_logs_dir, cache_db_path)
