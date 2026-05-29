from Daily_bot.models import Candidate
from Daily_bot.risk.guards import select_affordable_targets, trim_targets


def test_trim_targets_allows_all_candidates_when_max_buy_count_and_budget_are_unlimited():
    candidates = [
        Candidate(ticker="005930", price=10_000, expect_price=10_100),
        Candidate(ticker="000660", price=20_000, expect_price=20_100),
    ]

    result = trim_targets(candidates, max_buy_count=0, budget_per_stock_krw=0, sell_tick_offset=1)

    assert [candidate.ticker for candidate in result] == ["005930", "000660"]


def test_select_affordable_targets_skips_expensive_candidates_and_fills_with_cheaper_ones():
    candidates = [
        Candidate(ticker="111111", price=70_000, expect_price=71_000),
        Candidate(ticker="222222", price=40_000, expect_price=41_000),
        Candidate(ticker="333333", price=40_000, expect_price=41_000),
    ]

    result = select_affordable_targets(
        candidates,
        max_buy_count=2,
        available_cash_krw=100_000,
        budget_per_stock_krw=0,
        sell_tick_offset=1,
    )

    assert [candidate.ticker for candidate in result] == ["222222", "333333"]


def test_select_affordable_targets_falls_back_to_smaller_affordable_set():
    candidates = [
        Candidate(ticker="281820", price=63_600, expect_price=64_400),
        Candidate(ticker="383220", price=72_000, expect_price=72_700),
        Candidate(ticker="195870", price=90_200, expect_price=90_700),
    ]

    result = select_affordable_targets(
        candidates,
        max_buy_count=3,
        available_cash_krw=133_618,
        budget_per_stock_krw=0,
        sell_tick_offset=1,
    )

    assert [candidate.ticker for candidate in result] == ["281820"]
