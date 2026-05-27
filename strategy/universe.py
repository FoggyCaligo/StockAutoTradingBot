from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import]

from models import Candidate


@dataclass
class UniverseConfig:
    min_price: int
    max_price: int
    min_market_cap_krw: int
    min_trading_value_krw: int
    csv_path: str | None = None


DEFAULT_KOSPI200_CSV = Path("data/kospi200.csv")


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


def _load_local_universe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Local universe CSV not found: {path}")
    df = pd.read_csv(path, dtype=str)
    df = df.fillna("")
    for numeric_col in ["Close", "Open", "Marcap", "Amount", "TradingValue", "market_cap", "trading_value"]:
        if numeric_col in df.columns:
            series = pd.to_numeric(df[numeric_col].astype(str).str.replace(",", ""), errors="coerce")
            series = pd.Series(series).fillna(0).astype(int)
            df[numeric_col] = series
    return df



def get_kospi200_list(csv_path: str | None = None) -> pd.DataFrame:
    local_path = Path(csv_path) if csv_path else DEFAULT_KOSPI200_CSV
    if local_path.exists():
        return _load_local_universe(local_path)

    try:
        import FinanceDataReader as fdr  # type: ignore[import]

        df = fdr.StockListing("KOSPI")
        return df
    except Exception as exc:
        raise RuntimeError(
            "Failed to load universe. Provide data/kospi200.csv or install FinanceDataReader with network access."
        ) from exc


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
        df["trend_ok"] = df["trend_ok"].astype(bool)
        result = df.loc[df["trend_ok"]]
        assert isinstance(result, pd.DataFrame)
        return result

    try:
        import FinanceDataReader as fdr  # type: ignore[import]

        trend_ok = []
        today = pd.Timestamp("today").normalize()
        start = today - pd.Timedelta(days=90)
        for _, row in df.iterrows():
            ticker = _safe_str(row.get("Code") or row.get("Symbol") or row.get("code") or "")
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
    df = get_kospi200_list(cfg.csv_path)
    df = filter_by_price(df, cfg.min_price, cfg.max_price)
    df = filter_by_market_cap(df, cfg.min_market_cap_krw)
    df = filter_by_trading_value(df, cfg.min_trading_value_krw)
    df = filter_by_trend(df, trend_enabled)

    candidates: dict[str, Candidate] = {}
    for _, row in df.iterrows():
        ticker = str(row.get("Code") or row.get("Symbol") or row.get("code") or "").zfill(6)
        if not ticker.strip():
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
