from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_code(value: Any) -> str:
    code = _safe_str(value)
    if code.endswith(".KS") or code.endswith(".KQ"):
        code = code.split(".")[0]
    return code.zfill(6) if code else ""


@dataclass(frozen=True)
class HistoricalMarketData:
    listing: pd.DataFrame
    histories: dict[str, pd.DataFrame]


class HistoricalKrxDataProvider:
    def __init__(self, source: str = "auto", listing_market: str = "KOSPI200"):
        self.source = source.lower()
        self.listing_market = listing_market.upper()

    def load(self, start: str, end: str) -> HistoricalMarketData:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        listing = self._load_listing()
        histories: dict[str, pd.DataFrame] = {}

        history_start = start_date - timedelta(days=220)
        history_end = end_date + timedelta(days=1)
        for row in listing.to_dict(orient="records"):
            code = _normalize_code(row.get("Code") or row.get("Symbol") or row.get("code"))
            if not code:
                continue
            history = self._load_price_history(code, history_start, history_end)
            if history.empty:
                continue
            histories[code] = history

        return HistoricalMarketData(listing=listing, histories=histories)

    def _load_listing(self) -> pd.DataFrame:
        if self.source in {"auto", "fdr"}:
            try:
                import FinanceDataReader as fdr  # type: ignore[import]

                listing = fdr.StockListing(self.listing_market)
                if isinstance(listing, pd.DataFrame) and not listing.empty:
                    return listing
            except Exception:
                if self.source == "fdr":
                    raise

        fallback_path = self._local_listing_fallback_path()
        if fallback_path is not None:
            listing = pd.read_csv(fallback_path)
            if isinstance(listing, pd.DataFrame) and not listing.empty:
                return listing

        raise RuntimeError(f"Unable to load listing for market={self.listing_market}. Install FinanceDataReader or choose a supported source.")

    @staticmethod
    def _local_listing_fallback_path() -> Path | None:
        repo_root = Path(__file__).resolve().parents[4]
        candidates = [repo_root / "Weekly_bot" / "data" / "kospi200_latest.csv"]
        for path in candidates:
            if path.exists():
                return path
        return None

    def _load_price_history(self, code: str, start: date, end: date) -> pd.DataFrame:
        errors: list[str] = []

        if self.source in {"auto", "fdr"}:
            try:
                import FinanceDataReader as fdr  # type: ignore[import]

                history = fdr.DataReader(code, start=start, end=end)
                normalized = self._normalize_history(history)
                if not normalized.empty:
                    return normalized
            except Exception as exc:
                errors.append(f"fdr:{exc}")
                if self.source == "fdr":
                    raise

        if self.source in {"auto", "yfinance"}:
            try:
                import yfinance as yf  # type: ignore[import]

                history = yf.download(
                    f"{code}.KS",
                    start=start.isoformat(),
                    end=end.isoformat(),
                    auto_adjust=False,
                    progress=False,
                )
                normalized = self._normalize_history(history)
                if not normalized.empty:
                    return normalized
            except Exception as exc:
                errors.append(f"yfinance:{exc}")
                if self.source == "yfinance":
                    raise

        if errors:
            raise RuntimeError(f"Unable to load price history for {code}: {'; '.join(errors)}")
        return pd.DataFrame()

    @staticmethod
    def _normalize_history(history: pd.DataFrame | Any) -> pd.DataFrame:
        if not isinstance(history, pd.DataFrame) or history.empty:
            return pd.DataFrame()

        df = history.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(col[0]) for col in df.columns]

        rename_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "change": "Change",
        }
        normalized_columns = {}
        for column in df.columns:
            key = str(column).strip()
            lower = key.lower()
            normalized_columns[column] = rename_map.get(lower, key)
        df = df.rename(columns=normalized_columns)

        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(df.columns):
            return pd.DataFrame()

        df = df.loc[:, [col for col in ["Open", "High", "Low", "Close", "Volume", "Change"] if col in df.columns]].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        if "Change" not in df.columns:
            df["Change"] = df["Close"].pct_change().fillna(0.0)
        df["Change"] = pd.to_numeric(df["Change"], errors="coerce").fillna(0.0)
        return df
