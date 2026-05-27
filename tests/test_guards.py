from models import Candidate
from risk.guards import trim_targets


def test_trim_targets_allows_all_candidates_when_max_buy_count_and_budget_are_unlimited():
    candidates = [
        Candidate(ticker="005930", price=10_000, expect_price=10_100),
        Candidate(ticker="000660", price=20_000, expect_price=20_100),
    ]

    result = trim_targets(candidates, max_buy_count=0, budget_per_stock_krw=0, sell_tick_offset=1)

    assert [candidate.ticker for candidate in result] == ["005930", "000660"]
