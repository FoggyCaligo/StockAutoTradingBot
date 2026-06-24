import pandas as pd

from Daily_bot.models import Candidate
from Daily_bot.strategy.signal import final_filter, min_expected_return_with_spread
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


def test_final_filter_excludes_candidates_that_rose_fifteen_percent_or_more_previous_day():
    candidates = [
        Candidate(
            ticker="005930",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.7,
            spread_percent=0.2,
            prev_day_change_percent=14.99,
            trend_ok=True,
        ),
        Candidate(
            ticker="000660",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.7,
            spread_percent=0.2,
            prev_day_change_percent=15.0,
            trend_ok=True,
        ),
    ]

    result = final_filter(
        candidates,
        min_expected_return_percent=0.6,
        sell_tick_offset=1,
        max_spread_percent=0.7,
        max_prev_day_change_percent=15.0,
    )

    assert [candidate.ticker for candidate in result] == ["005930"]


def test_final_filter_keeps_only_candidates_that_dropped_two_percent_or_more_previous_day():
    candidates = [
        Candidate(
            ticker="AAA",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.7,
            spread_percent=0.2,
            prev_day_change_percent=-2.0,
            trend_ok=True,
        ),
        Candidate(
            ticker="BBB",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.7,
            spread_percent=0.2,
            prev_day_change_percent=-1.99,
            trend_ok=True,
        ),
    ]

    result = final_filter(
        candidates,
        min_expected_return_percent=0.6,
        sell_tick_offset=1,
        max_spread_percent=0.7,
        min_prev_day_change_percent=-2.0,
    )

    assert [candidate.ticker for candidate in result] == ["AAA"]


def test_min_expected_return_with_spread_raises_threshold_for_wider_spread():
    assert min_expected_return_with_spread(0.3, 0.5, 1.2) == 0.6
    assert min_expected_return_with_spread(0.3, 0.2, 1.2) == 0.3


def test_final_filter_requires_higher_expected_return_when_spread_is_wider():
    candidates = [
        Candidate(
            ticker="AAA",
            price=10_000,
            expect_price=10_100,
            expect_revenue_percent=0.55,
            spread_percent=0.5,
            trend_ok=True,
        ),
        Candidate(
            ticker="BBB",
            price=10_000,
            expect_price=10_110,
            expect_revenue_percent=0.65,
            spread_percent=0.5,
            trend_ok=True,
        ),
    ]

    result = final_filter(
        candidates,
        min_expected_return_percent=0.3,
        sell_tick_offset=1,
        max_spread_percent=0.7,
        spread_expected_return_multiplier=1.2,
    )

    assert [candidate.ticker for candidate in result] == ["BBB"]
