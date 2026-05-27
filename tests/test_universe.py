from pathlib import Path

from strategy.universe import UniverseConfig, get_candidates, get_kospi200_list


def test_get_kospi200_list_loads_local_csv():
    csv_path = Path("data/kospi200.csv")
    df = get_kospi200_list(str(csv_path))

    assert not df.empty
    assert "Code" in df.columns
    assert df["Code"].str.len().ge(6).all()


def test_get_candidates_returns_dict_with_csv_source():
    cfg = UniverseConfig(
        min_price=10000,
        max_price=500000,
        min_market_cap_krw=100000000000,
        min_trading_value_krw=10000000000,
        csv_path="data/kospi200.csv",
    )
    candidates = get_candidates(cfg, trend_enabled=False)

    assert isinstance(candidates, dict)
    assert "005930" in candidates
    assert candidates["005930"].price == 65000
