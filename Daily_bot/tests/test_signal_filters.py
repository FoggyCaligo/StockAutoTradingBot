import pandas as pd

from Daily_bot.models import Candidate
from Daily_bot.strategy.signal import final_filter
from Daily_bot.strategy.universe import _trend_ok_from_series


def test_final_filter_uses_target_sell_price_when_spread_filter_is_disabled():
    candidates = [
        Candidate(
            ticker="005930",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.7,
            spread_percent=9.9,
            trend_ok=True,
        ),
        Candidate(
            ticker="000660",
            price=10_000,
            expect_price=10_000,
            expect_revenue_percent=0.7,
            spread_percent=0.0,
            trend_ok=True,
        ),
    ]

    result = final_filter(candidates, min_expected_return_percent=0.6, sell_tick_offset=1, max_spread_percent=0.0)

    assert [candidate.ticker for candidate in result] == ["005930"]


def test_trend_ok_accepts_positive_ma5_slope_even_when_ma20_slope_is_not_positive():
    close_series = pd.Series([100] * 15 + [100, 100, 100, 100, 110])

    assert _trend_ok_from_series(close_series) is True


def test_trend_ok_accepts_positive_ma20_slope_even_when_ma5_slope_is_not_positive():
    close_series = pd.Series(
        [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 114, 114, 114, 114, 114, 114]
    )

    assert _trend_ok_from_series(close_series) is True
