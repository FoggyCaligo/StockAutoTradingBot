from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_KOSPI200_CSV = Path("data/kospi200.csv")
DEFAULT_KOSPI200_CACHE = Path("data/kospi200_latest.csv")
NUMERIC_COLUMNS = [
    "Close",
    "Open",
    "Marcap",
    "MarketCap",
    "Amount",
    "TradingValue",
    "market_cap",
    "trading_value",
]


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.fillna("").copy()
    for numeric_col in NUMERIC_COLUMNS:
        if numeric_col in df.columns:
            series = pd.to_numeric(df[numeric_col].astype(str).str.replace(",", ""), errors="coerce")
            df[numeric_col] = pd.Series(series).fillna(0).astype(int)
    return df


def _load_local_universe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Local universe CSV not found: {path}")
    return _coerce_numeric_columns(pd.read_csv(path, dtype=str))


def _cache_is_today(path: Path) -> bool:
    if not path.exists():
        return False
    modified_day = pd.Timestamp(path.stat().st_mtime, unit="s").normalize()
    return bool(modified_day == pd.Timestamp("today").normalize())


def _save_cache(df: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)


def _load_fdr_listing(source: str) -> pd.DataFrame:
    import FinanceDataReader as fdr  # type: ignore[import]

    candidates = [source]
    if source.upper() == "KOSPI200":
        candidates.extend(["KS200", "KOSPI"])

    last_error: Exception | None = None
    for market in candidates:
        try:
            df = fdr.StockListing(market)
            if isinstance(df, pd.DataFrame) and not df.empty:
                return _coerce_numeric_columns(df)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"FinanceDataReader failed to load source={source}") from last_error


def get_kospi200_list(
    csv_path: str | None = None,
    cache_path: str | None = None,
    source: str = "KOSPI200",
    refresh_daily: bool = True,
) -> pd.DataFrame:
    fallback_path = Path(csv_path) if csv_path else DEFAULT_KOSPI200_CSV
    cache = Path(cache_path) if cache_path else DEFAULT_KOSPI200_CACHE

    if refresh_daily:
        if _cache_is_today(cache):
            return _load_local_universe(cache)
        try:
            df = _load_fdr_listing(source)
            _save_cache(df, cache)
            return df
        except Exception as exc:
            warnings.warn(
                f"Daily universe refresh failed; falling back to local CSV/cache. reason={exc}",
                UserWarning,
            )

    if cache.exists():
        return _load_local_universe(cache)
    if fallback_path.exists():
        return _load_local_universe(fallback_path)

    raise RuntimeError(
        "Failed to load universe. Enable network access for FinanceDataReader or provide data/kospi200.csv."
    )
