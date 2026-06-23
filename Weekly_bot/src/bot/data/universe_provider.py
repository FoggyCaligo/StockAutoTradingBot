from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_KOSPI200_CSV = Path("data/kospi200.csv")
DEFAULT_KOSPI200_CACHE = Path("data/kospi200_latest.csv")
DEFAULT_BROAD_LISTING_CANDIDATES = [
    Path("data/kospi200_latest.csv"),
    Path("Daily_bot/data/kospi200_latest.csv"),
]
MIN_EXPECTED_KOSPI200_SIZE = 180
MAX_EXPECTED_KOSPI200_SIZE = 230
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


def _extract_codes(df: pd.DataFrame) -> set[str]:
    for column in ("Code", "code", "Symbol", "symbol", "ticker", "Ticker"):
        if column in df.columns:
            return {
                str(value).strip().replace(".KS", "").replace(".KQ", "").zfill(6)
                for value in df[column]
                if str(value).strip()
            }
    return set()


def _is_expected_kospi200_listing(df: pd.DataFrame, source: str) -> bool:
    if source.upper() not in {"KOSPI200", "KS200"}:
        return True
    codes = _extract_codes(df)
    return MIN_EXPECTED_KOSPI200_SIZE <= len(codes) <= MAX_EXPECTED_KOSPI200_SIZE


def _filter_listing_to_codes(df: pd.DataFrame, codes: set[str]) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame(columns=df.columns)
    code_column = next((column for column in ("Code", "code", "Symbol", "symbol", "ticker", "Ticker") if column in df.columns), None)
    if code_column is None:
        return pd.DataFrame(columns=df.columns)
    normalized_codes = df[code_column].astype(str).str.strip().str.replace(".KS", "", regex=False).str.replace(".KQ", "", regex=False).str.zfill(6)
    filtered = df.loc[normalized_codes.isin(codes)].copy()
    if code_column in filtered.columns:
        filtered[code_column] = filtered[code_column].astype(str).str.strip().str.replace(".KS", "", regex=False).str.replace(".KQ", "", regex=False).str.zfill(6)
        filtered = filtered.drop_duplicates(subset=[code_column]).sort_values(code_column)
    return filtered


def _load_fdr_listing(source: str) -> pd.DataFrame:
    import FinanceDataReader as fdr  # type: ignore[import]

    candidates = [source]
    if source.upper() == "KOSPI200":
        candidates.extend(["KS200"])

    last_error: Exception | None = None
    for market in candidates:
        try:
            df = fdr.StockListing(market)
            if isinstance(df, pd.DataFrame) and not df.empty:
                normalized = _coerce_numeric_columns(df)
                if _is_expected_kospi200_listing(normalized, market):
                    return normalized
                last_error = RuntimeError(
                    f"Unexpected {market} universe size: {len(_extract_codes(normalized))}"
                )
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"FinanceDataReader failed to load source={source}") from last_error


def _load_valid_component_codes(path: Path, source: str) -> set[str]:
    if not path.exists():
        return set()
    df = _load_local_universe(path)
    if not _is_expected_kospi200_listing(df, source):
        return set()
    return _extract_codes(df)


def _refresh_kospi200_from_broad_listing(fallback_path: Path, cache_path: Path) -> pd.DataFrame:
    component_codes = _load_valid_component_codes(fallback_path, "KOSPI200")
    if not component_codes:
        component_codes = _load_valid_component_codes(cache_path, "KOSPI200")
    if not component_codes:
        raise RuntimeError("No valid KOSPI200 component code source available for KOSPI rebuild.")

    broad_listing = _load_fdr_listing("KOSPI")
    filtered = _filter_listing_to_codes(broad_listing, component_codes)
    if not _is_expected_kospi200_listing(filtered, "KOSPI200"):
        raise RuntimeError(f"Filtered KOSPI listing has unexpected size: {len(_extract_codes(filtered))}")
    return filtered


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
            cached = _load_local_universe(cache)
            if _is_expected_kospi200_listing(cached, source):
                return cached
        try:
            df = _load_fdr_listing(source)
            _save_cache(df, cache)
            return df
        except Exception as exc:
            if source.upper() == "KOSPI200":
                try:
                    rebuilt = _refresh_kospi200_from_broad_listing(fallback_path, cache)
                    _save_cache(rebuilt, cache)
                    return rebuilt
                except Exception:
                    pass
            warnings.warn(
                f"Daily universe refresh failed; falling back to local CSV/cache. reason={exc}",
                UserWarning,
            )

    if cache.exists():
        cached = _load_local_universe(cache)
        if _is_expected_kospi200_listing(cached, source):
            return cached
    if fallback_path.exists():
        fallback = _load_local_universe(fallback_path)
        if _is_expected_kospi200_listing(fallback, source):
            return fallback

    raise RuntimeError(
        "Failed to load universe. Enable network access for FinanceDataReader or provide data/kospi200.csv."
    )
