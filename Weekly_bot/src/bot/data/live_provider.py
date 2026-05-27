from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from bot.data.base import MarketDataProvider
from bot.models import MarketSnapshot


def _load_daily_bot_modules():
    weekly_root = Path(__file__).resolve().parents[3]
    workspace_root = weekly_root.parent
    daily_bot_root = workspace_root / "Daily_bot"
    if str(daily_bot_root) not in sys.path:
        sys.path.insert(0, str(daily_bot_root))

    from broker.kiwoom_client import KiwoomClient  # type: ignore
    from strategy.universe import get_kospi200_list  # type: ignore
    from utils import get_tick_size  # type: ignore

    return KiwoomClient, get_kospi200_list, get_tick_size


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_code(value: Any) -> str:
    code = _safe_str(value)
    if code.endswith(".KS") or code.endswith(".KQ"):
        code = code.split(".")[0]
    return code.zfill(6) if code else ""


def _get_row_value(row: pd.Series, candidates: list[str]) -> Any:
    for key in candidates:
        if key in row and pd.notna(row[key]):
            return row[key]
    return None


class LiveKrxMarketDataProvider(MarketDataProvider):
    def __init__(self):
        load_dotenv()
        client_class, universe_loader, tick_size_fn = _load_daily_bot_modules()
        self.client = client_class()
        self.client.auth()
        self._get_kospi200_list = universe_loader
        self._get_tick_size = tick_size_fn
        self._universe_df: pd.DataFrame | None = None
        self._universe_rows_by_code: dict[str, pd.Series] = {}
        self._history_cache: dict[str, pd.DataFrame] = {}
        self._snapshots: list[MarketSnapshot] | None = None

    def load_snapshots(self) -> list[MarketSnapshot]:
        if self._snapshots is not None:
            return self._snapshots

        universe_df = self._load_universe_df()
        snapshots: list[MarketSnapshot] = []

        prefiltered = self._prefilter_listing(universe_df)
        for _, row in prefiltered.iterrows():
            code = _normalize_code(_get_row_value(row, ["Code", "Symbol", "code"]))
            if not code:
                continue

            try:
                snapshot = self._build_snapshot(row, code)
            except Exception as exc:
                print(f"Skipping live snapshot for {code} due to error: {exc}")
                continue

            snapshots.append(snapshot)

        self._snapshots = snapshots
        return snapshots

    def get_snapshot(self, code: str) -> MarketSnapshot | None:
        normalized = _normalize_code(code)
        if self._snapshots is not None:
            cached_snapshot = next((snapshot for snapshot in self._snapshots if snapshot.code == normalized), None)
            if cached_snapshot is not None:
                return cached_snapshot

        row = self._get_universe_row(normalized)
        if row is None:
            return None
        try:
            return self._build_snapshot(row, normalized)
        except Exception as exc:
            print(f"Skipping live snapshot for {normalized} due to error: {exc}")
            return None

    def _load_universe_df(self) -> pd.DataFrame:
        if self._universe_df is None:
            self._universe_df = self._get_kospi200_list(source="KOSPI200", refresh_daily=True)
        return self._universe_df

    def _get_universe_row(self, code: str) -> pd.Series | None:
        if code in self._universe_rows_by_code:
            return self._universe_rows_by_code[code]

        universe_df = self._load_universe_df()
        for _, row in universe_df.iterrows():
            normalized = _normalize_code(_get_row_value(row, ["Code", "Symbol", "code"]))
            if not normalized:
                continue
            if normalized not in self._universe_rows_by_code:
                self._universe_rows_by_code[normalized] = row
            if normalized == code:
                return row
        return None

    def _prefilter_listing(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        market_cap = pd.to_numeric(
            df.apply(lambda row: _get_row_value(row, ["Marcap", "MarketCap", "market_cap"]), axis=1),
            errors="coerce",
        ).fillna(0)
        change_pct = pd.to_numeric(
            df.apply(lambda row: _get_row_value(row, ["ChagesRatio", "ChangesRatio", "ChangeRate", "change_pct"]), axis=1),
            errors="coerce",
        ).fillna(0.0)
        volume = pd.to_numeric(
            df.apply(lambda row: _get_row_value(row, ["Volume", "volume"]), axis=1),
            errors="coerce",
        ).fillna(0)
        turnover = pd.to_numeric(
            df.apply(lambda row: _get_row_value(row, ["Amount", "TradingValue", "trading_value"]), axis=1),
            errors="coerce",
        ).fillna(0)

        return df.loc[
            (market_cap >= 300_000_000_000)
            & (change_pct >= -7.0)
            & (change_pct <= -2.0)
            & (volume >= 10_000)
            & (turnover >= 500_000_000)
        ]

    def _build_snapshot(self, row: pd.Series, code: str) -> MarketSnapshot:
        history = self._load_history(code)
        close_series = history["Close"].astype(float)
        if len(close_series) < 121:
            raise RuntimeError("insufficient price history")

        hoga = self.client.get_20hoga(code)
        current_price = int(hoga.current_price)
        tick_size = int(self._get_tick_size(current_price))
        best_bid = hoga.bids[0].price if hoga.bids else current_price - tick_size
        best_ask = hoga.asks[0].price if hoga.asks else current_price + tick_size

        return MarketSnapshot(
            code=code,
            name=_safe_str(_get_row_value(row, ["Name", "name"])) or code,
            is_kospi200=True,
            market_cap_krw=_safe_int(_get_row_value(row, ["Marcap", "MarketCap", "market_cap"])),
            current_price=current_price,
            change_pct=_safe_float(_get_row_value(row, ["ChagesRatio", "ChangesRatio", "ChangeRate", "change_pct"])),
            turnover_krw=_safe_int(_get_row_value(row, ["Amount", "TradingValue", "trading_value"])),
            volume=_safe_int(_get_row_value(row, ["Volume", "volume"])),
            ma20=float(close_series.rolling(20).mean().iloc[-1]),
            ma30=float(close_series.rolling(30).mean().iloc[-1]),
            ma30_prev=float(close_series.rolling(30).mean().iloc[-2]),
            ma50=float(close_series.rolling(50).mean().iloc[-1]),
            ma50_prev=float(close_series.rolling(50).mean().iloc[-2]),
            ma120=float(close_series.rolling(120).mean().iloc[-1]),
            ma120_prev=float(close_series.rolling(120).mean().iloc[-2]),
            bid_price_1=int(best_bid),
            ask_price_1=int(best_ask),
            tick_size=tick_size,
        )

    def _load_history(self, code: str) -> pd.DataFrame:
        if code in self._history_cache:
            return self._history_cache[code]

        import FinanceDataReader as fdr  # type: ignore[import]

        end = datetime.now().date()
        start = end - pd.Timedelta(days=220)
        history = fdr.DataReader(code, start=start, end=end)
        if not isinstance(history, pd.DataFrame) or history.empty or "Close" not in history.columns:
            raise RuntimeError("price history unavailable")
        self._history_cache[code] = history
        return history
