from pathlib import Path

import pandas as pd

from Daily_bot.strategy.universe import UniverseConfig, get_candidates, get_kospi200_list


def test_get_kospi200_list_loads_local_csv_when_refresh_daily_false():
    csv_path = Path("data/kospi200.csv")
    df = get_kospi200_list(str(csv_path), refresh_daily=False)

    assert not df.empty
    assert "Code" in df.columns
    assert df["Code"].str.len().ge(6).all()


def test_get_candidates_uses_csv_when_refresh_daily_false(tmp_path):
    cache_path = tmp_path / "missing_cache.csv"
    cfg = UniverseConfig(
        min_price=10000,
        max_price=500000,
        min_market_cap_krw=100000000000,
        min_trading_value_krw=10000000000,
        csv_path="data/kospi200.csv",
        cache_path=str(cache_path),
        refresh_daily=False,
    )
    candidates = get_candidates(cfg, trend_enabled=False)

    assert isinstance(candidates, dict)
    assert "005930" in candidates
    assert candidates["005930"].price == 65000


def test_get_kospi200_list_prefers_daily_cache_when_refresh_enabled(tmp_path):
    fallback_csv = tmp_path / "fallback.csv"
    fallback_csv.write_text(
        "Code,Name,Close,Marcap,Amount\n005930,CSV Samsung,65000,500000000000,10000000000\n",
        encoding="utf-8",
    )

    cache_csv = tmp_path / "cache.csv"
    cache_df = pd.DataFrame(
        [
            {
                "Code": "005930",
                "Name": "Cached Samsung",
                "Close": 307000,
                "Marcap": 1794807532656000,
                "Amount": 10730431777324,
            }
        ]
    )
    cache_df.to_csv(cache_csv, index=False)

    df = get_kospi200_list(
        csv_path=str(fallback_csv),
        cache_path=str(cache_csv),
        refresh_daily=True,
    )

    assert df.iloc[0]["Name"] == "Cached Samsung"
    assert int(df.iloc[0]["Close"]) == 307000
