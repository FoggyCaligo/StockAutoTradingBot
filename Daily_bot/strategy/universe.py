from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import]

from Daily_bot.models import Candidate


@dataclass
class UniverseConfig:
    min_price: int
    max_price: int
    min_market_cap_krw: int
    min_trading_value_krw: int
    csv_path: str | None = None
    cache_path: str | None = None
    source: str = "KOSPI200"
    refresh_daily: bool = True


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


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        try:
            return int(float(str(value).replace(",", "").strip()))
        except ValueError:
            return 0


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _normalize_code(value: Any) -> str:
    code = str(value or "").strip()
    if code.endswith(".KS") or code.endswith(".KQ"):
        code = code.split(".")[0]
    return code.zfill(6) if code else ""


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
        # FinanceDataReader versions differ. Try the narrow source first, then
        # fall back to broad KOSPI listing so the bot can still build a daily
        # refreshed universe.
        candidates.extend(["KS200", "KOSPI"])

    last_error: Exception | None = None
    for market in candidates:
        try:
            df = fdr.StockListing(market)
            if isinstance(df, pd.DataFrame) and not df.empty:
                return _coerce_numeric_columns(df)
        except Exception as exc:  # pragma: no cover - network/data source dependent
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


def filter_by_price(df: pd.DataFrame, min_price: int, max_price: int) -> pd.DataFrame:
    price_col = None
    for candidate in ["Close", "Open", "현재가", "price", "Price"]:
        if candidate in df.columns:
            price_col = candidate
            break
    if price_col is None:
        return df
    result = df.loc[(df[price_col] >= min_price) & (df[price_col] <= max_price)]
    assert isinstance(result, pd.DataFrame)
    return result


def filter_by_market_cap(df: pd.DataFrame, min_market_cap_krw: int) -> pd.DataFrame:
    for col in ["Marcap", "MarketCap", "market_cap", "시가총액"]:
        if col in df.columns:
            result = df.loc[df[col] >= min_market_cap_krw]
            assert isinstance(result, pd.DataFrame)
            return result
    return df


def filter_by_trading_value(df: pd.DataFrame, min_trading_value_krw: int) -> pd.DataFrame:
    for col in ["Amount", "TradingValue", "trading_value", "거래대금"]:
        if col in df.columns:
            result = df.loc[df[col] >= min_trading_value_krw]
            assert isinstance(result, pd.DataFrame)
            return result
    return df


def _trend_ok_from_series(close_series: pd.Series) -> bool:
    if len(close_series) < 21:
        return False
    ma5 = close_series.rolling(5).mean().iloc[-1]
    ma20 = close_series.rolling(20).mean().iloc[-1]
    ma20_slope = close_series.rolling(20).mean().diff().iloc[-1]
    price = close_series.iloc[-1]
    return bool(price > ma20 and ma5 > ma20 and ma20_slope > 0)


def filter_by_trend(df: pd.DataFrame, enabled: bool = True) -> pd.DataFrame:
    if not enabled:
        df["trend_ok"] = True
        return df

    if "trend_ok" in df.columns:
        trend = df["trend_ok"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
        df["trend_ok"] = trend
        result = df.loc[df["trend_ok"]]
        assert isinstance(result, pd.DataFrame)
        return result

    try:
        import FinanceDataReader as fdr  # type: ignore[import]

        trend_ok = []
        today = pd.Timestamp("today").normalize()
        start = today - pd.Timedelta(days=90)
        for _, row in df.iterrows():
            ticker = _normalize_code(row.get("Code") or row.get("Symbol") or row.get("code") or "")
            if not ticker:
                trend_ok.append(False)
                continue
            try:
                daily = fdr.DataReader(ticker, start=start, end=today)
                if "Close" not in daily.columns:
                    trend_ok.append(False)
                    continue
                trend_ok.append(_trend_ok_from_series(daily["Close"]))
            except Exception:
                trend_ok.append(False)

        df["trend_ok"] = trend_ok
        result = df.loc[df["trend_ok"]]
        assert isinstance(result, pd.DataFrame)
        return result
    except Exception:
        warnings.warn(
            "FinanceDataReader unavailable or universe trend filter failed. Passing all candidates through trend filter.",
            UserWarning,
        )
        df["trend_ok"] = True
        return df


def get_candidates(cfg: UniverseConfig, trend_enabled: bool = True) -> dict[str, Candidate]:
    df = get_kospi200_list(
        cfg.csv_path,
        cache_path=cfg.cache_path,
        source=cfg.source,
        refresh_daily=cfg.refresh_daily,
    )
    df = filter_by_price(df, cfg.min_price, cfg.max_price)
    df = filter_by_market_cap(df, cfg.min_market_cap_krw)
    df = filter_by_trading_value(df, cfg.min_trading_value_krw)
    df = filter_by_trend(df, trend_enabled)

    candidates: dict[str, Candidate] = {}
    for _, row in df.iterrows():
        ticker = _normalize_code(row.get("Code") or row.get("Symbol") or row.get("code") or "")
        if not ticker:
            continue

        price = _safe_int(row.get("Close") or row.get("Open") or row.get("price") or row.get("Price"))
        market_cap = _safe_int(row.get("Marcap") or row.get("MarketCap") or row.get("market_cap"))
        trading_value = _safe_int(row.get("Amount") or row.get("TradingValue") or row.get("trading_value"))
        trend_ok = bool(row.get("trend_ok", True))

        candidates[ticker] = Candidate(
            ticker=ticker,
            name=_safe_str(row.get("Name") or row.get("name") or row.get("회사명")),
            price=price,
            market_cap=market_cap,
            trading_value=trading_value,
            trend_ok=trend_ok,
        )
    return candidates
