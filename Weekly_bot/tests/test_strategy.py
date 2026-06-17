from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.config import load_config
from bot.data.csv_provider import CsvMarketDataProvider
from bot.models import MarketSnapshot
from bot.strategy.weekly_pullback import WeeklyPullbackStrategy


def test_select_candidates_from_sample_data():
    config = load_config(ROOT / "config/strategy.yaml")
    provider = CsvMarketDataProvider(ROOT / "data/sample_market_snapshot.csv")
    strategy = WeeklyPullbackStrategy(config)

    candidates = strategy.select_candidates(provider.load_snapshots())

    codes = {c.snapshot.code for c in candidates}
    assert "005930" in codes
    assert "000660" in codes
    assert "068270" not in codes
    assert "051910" not in codes  # 등락률 -1% 조건 미달
    assert "123456" not in codes  # KOSPI200 아님


def test_envelope_ma_days_changes_envelope_basis():
    base_config = load_config(ROOT / "config/strategy.yaml")
    snapshot = MarketSnapshot(
        code="005930",
        name="Samsung",
        is_kospi200=True,
        market_cap_krw=400_000_000_000,
        current_price=100,
        change_pct=-3.0,
        turnover_krw=10_000_000_000,
        volume=0,
        ma20=110.0,
        ma30=120.0,
        ma30_prev=119.0,
        ma50=130.0,
        ma50_prev=129.0,
        ma120=140.0,
        ma120_prev=139.0,
        bid_price_1=99,
        ask_price_1=100,
        tick_size=1,
    )

    strategy_20 = WeeklyPullbackStrategy(replace(base_config, envelope_ma_days=20, envelope_lower_pct=3.0))
    strategy_30 = WeeklyPullbackStrategy(replace(base_config, envelope_ma_days=30, envelope_lower_pct=3.0))

    assert round(strategy_20._lower_envelope(snapshot), 4) == 106.7
    assert round(strategy_30._lower_envelope(snapshot), 4) == 116.4


def test_spread_filter_can_be_disabled():
    base_config = load_config(ROOT / "config/strategy.yaml")
    strategy = WeeklyPullbackStrategy(replace(base_config, max_spread_ticks=0))
    snapshot = MarketSnapshot(
        code="005930",
        name="Samsung",
        is_kospi200=True,
        market_cap_krw=400_000_000_000,
        current_price=100,
        change_pct=-3.0,
        turnover_krw=10_000_000_000,
        volume=0,
        ma20=110.0,
        ma30=120.0,
        ma30_prev=119.0,
        ma50=130.0,
        ma50_prev=129.0,
        ma120=140.0,
        ma120_prev=139.0,
        bid_price_1=90,
        ask_price_1=110,
        tick_size=1,
    )

    passed, _ = strategy._passes_filters(snapshot)
    assert passed is True


def test_select_candidates_is_not_capped_by_max_positions():
    base_config = load_config(ROOT / "config/strategy.yaml")
    strategy = WeeklyPullbackStrategy(replace(base_config, max_positions=1))
    snapshots = [
        MarketSnapshot(
            code=f"{index:06d}",
            name=f"stock-{index}",
            is_kospi200=True,
            market_cap_krw=400_000_000_000,
            current_price=100,
            change_pct=-3.0,
            turnover_krw=10_000_000_000,
            volume=0,
            ma20=110.0,
            ma30=120.0,
            ma30_prev=119.0,
            ma50=130.0,
            ma50_prev=129.0,
            ma120=140.0,
            ma120_prev=139.0,
            bid_price_1=99,
            ask_price_1=100,
            tick_size=1,
        )
        for index in range(3)
    ]

    candidates = strategy.select_candidates(snapshots)

    assert len(candidates) == 3
