from Daily_bot.models import Candidate
from Daily_bot.risk.guards import passes_orderbook_ask_depth_ratio, select_affordable_targets, trim_targets


def test_trim_targets_allows_all_candidates_when_max_buy_count_and_budget_are_unlimited():
    candidates = [
        Candidate(ticker="005930", price=10_000, expect_price=10_100),
        Candidate(ticker="000660", price=20_000, expect_price=20_100),
    ]

    result = trim_targets(candidates, max_buy_count=0, budget_per_stock_krw=0, sell_tick_offset=1)

    assert [candidate.ticker for candidate in result] == ["005930", "000660"]


def test_select_affordable_targets_skips_expensive_candidates_and_fills_with_cheaper_ones():
    candidates = [
        Candidate(ticker="111111", price=70_000, expect_price=71_000, ask_depth_5_amount_krw=500_000),
        Candidate(ticker="222222", price=40_000, expect_price=41_000, ask_depth_5_amount_krw=500_000),
        Candidate(ticker="333333", price=40_000, expect_price=41_000, ask_depth_5_amount_krw=500_000),
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
        Candidate(ticker="281820", price=63_600, expect_price=64_400, ask_depth_5_amount_krw=500_000),
        Candidate(ticker="383220", price=72_000, expect_price=72_700, ask_depth_5_amount_krw=500_000),
        Candidate(ticker="195870", price=90_200, expect_price=90_700, ask_depth_5_amount_krw=500_000),
    ]

    result = select_affordable_targets(
        candidates,
        max_buy_count=3,
        available_cash_krw=133_618,
        budget_per_stock_krw=0,
        sell_tick_offset=1,
    )

    assert [candidate.ticker for candidate in result] == ["383220"]


def test_passes_orderbook_ask_depth_ratio_blocks_candidate_when_order_is_too_large_for_top5_asks():
    candidate = Candidate(
        ticker="005930",
        price=10_000,
        expect_price=10_100,
        ask_depth_5_amount_krw=200_000,
    )

    assert passes_orderbook_ask_depth_ratio(candidate, estimated_cost_krw=50_000, max_orderbook_ask_depth_ratio=0.20) is False
    assert passes_orderbook_ask_depth_ratio(candidate, estimated_cost_krw=40_000, max_orderbook_ask_depth_ratio=0.20) is True


def test_select_affordable_targets_skips_candidate_that_would_consume_too_much_top5_ask_depth():
    candidates = [
        Candidate(ticker="111111", price=10_000, expect_price=10_100, ask_depth_5_amount_krw=100_000),
        Candidate(ticker="222222", price=10_000, expect_price=10_100, ask_depth_5_amount_krw=300_000),
    ]

    result = select_affordable_targets(
        candidates,
        max_buy_count=2,
        available_cash_krw=100_000,
        budget_per_stock_krw=50_000,
        sell_tick_offset=1,
        max_orderbook_ask_depth_ratio=0.20,
    )

    assert [candidate.ticker for candidate in result] == ["222222"]
