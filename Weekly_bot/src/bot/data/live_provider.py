from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from bot.data.base import MarketDataProvider
from bot.data.universe_provider import get_kospi200_list
from bot.integrations.kiwoom_client import KiwoomClient
from bot.models import MarketSnapshot
from bot.utils import get_tick_size


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
        self.client = KiwoomClient()
        self.client.auth()
        self._universe_df: pd.DataFrame | None = None
        self._universe_rows_by_code: dict[str, pd.Series] = {}
        self._listing_df: pd.DataFrame | None = None
        self._listing_rows_by_code: dict[str, pd.Series] = {}
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
        row = self._get_universe_row(normalized)
        if row is None:
            row = self._get_listing_row(normalized)
        if row is None:
            return None
        try:
            snapshot = self._build_snapshot(row, normalized)
            self._refresh_cached_snapshot(snapshot)
            return snapshot
        except Exception as exc:
            print(f"Skipping live snapshot for {normalized} due to error: {exc}")
            return None

    def _refresh_cached_snapshot(self, snapshot: MarketSnapshot) -> None:
        if self._snapshots is None:
            return
        refreshed: list[MarketSnapshot] = []
        replaced = False
        for cached_snapshot in self._snapshots:
            if cached_snapshot.code == snapshot.code:
                refreshed.append(snapshot)
                replaced = True
            else:
                refreshed.append(cached_snapshot)
        if not replaced:
            refreshed.append(snapshot)
        self._snapshots = refreshed

    def _load_universe_df(self) -> pd.DataFrame:
        if self._universe_df is None:
            self._universe_df = get_kospi200_list(source="KOSPI200", refresh_daily=True)
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

    def _load_listing_df(self) -> pd.DataFrame:
        if self._listing_df is not None:
            return self._listing_df

        try:
            import FinanceDataReader as fdr  # type: ignore[import]

            listing = fdr.StockListing("KOSPI")
            if isinstance(listing, pd.DataFrame) and not listing.empty:
                self._listing_df = listing
                return listing
        except Exception:
            pass

        for candidate in (
            Path("data/kospi200_latest.csv"),
            Path("Daily_bot/data/kospi200_latest.csv"),
            Path("Weekly_bot/data/kospi200_latest.csv"),
        ):
            if candidate.exists():
                listing = pd.read_csv(candidate, dtype=str)
                if isinstance(listing, pd.DataFrame) and not listing.empty:
                    self._listing_df = listing
                    return listing

        self._listing_df = pd.DataFrame()
        return self._listing_df

    def _get_listing_row(self, code: str) -> pd.Series | None:
        if code in self._listing_rows_by_code:
            return self._listing_rows_by_code[code]

        listing_df = self._load_listing_df()
        if listing_df.empty:
            return None
        for _, row in listing_df.iterrows():
            normalized = _normalize_code(_get_row_value(row, ["Code", "Symbol", "code"]))
            if not normalized:
                continue
            if normalized not in self._listing_rows_by_code:
                self._listing_rows_by_code[normalized] = row
            if normalized == code:
                return row
        return None

    def _prefilter_listing(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def _build_snapshot(self, row: pd.Series, code: str) -> MarketSnapshot:
        history = self._load_history(code)
        close_series = history["Close"].astype(float)
        if len(close_series) < 121:
            raise RuntimeError("insufficient price history")
        if len(history.index) < 2:
            raise RuntimeError("insufficient daily bars for previous-close snapshot")

        prev_bar = history.iloc[-1]
        prev_close = _safe_int(prev_bar.get("Close"))
        if prev_close <= 0:
            raise RuntimeError("previous close unavailable")

        prev_prev_close = _safe_float(history.iloc[-2].get("Close"))
        prev_change_pct = ((prev_close / prev_prev_close) - 1.0) * 100.0 if prev_prev_close > 0 else 0.0
        prev_volume = _safe_int(prev_bar.get("Volume"))
        prev_turnover = int(prev_close * prev_volume)

        hoga = self.client.get_20hoga(code)
        tick_size = int(get_tick_size(prev_close))
        best_bid = hoga.bids[0].price if hoga.bids else prev_close - tick_size
        best_ask = hoga.asks[0].price if hoga.asks else prev_close + tick_size

        return MarketSnapshot(
            code=code,
            name=_safe_str(_get_row_value(row, ["Name", "name"])) or code,
            is_kospi200=True,
            market_cap_krw=_safe_int(_get_row_value(row, ["Marcap", "MarketCap", "market_cap"])),
            current_price=prev_close,
            change_pct=prev_change_pct,
            turnover_krw=prev_turnover,
            volume=prev_volume,
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
