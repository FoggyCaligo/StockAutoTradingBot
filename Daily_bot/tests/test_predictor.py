from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price, predict_price_from_hoga
from Daily_bot.strategy.signal import calc_expected_return


def test_predict_price_returns_int():
    snapshot = HogaSnapshot(
        ticker="000000",
        current_price=10000,
        bids=[HogaLevel(9950, 100), HogaLevel(9900, 100)],
        asks=[HogaLevel(10050, 100), HogaLevel(10100, 100)],
    )
    assert isinstance(predict_price_from_hoga(snapshot), int)


def test_expected_return_uses_target_sell_price_not_raw_expect_price():
    snapshot = HogaSnapshot(
        ticker="000000",
        current_price=10000,
        bids=[HogaLevel(9950, 100), HogaLevel(9900, 100)],
        asks=[HogaLevel(10050, 100), HogaLevel(10100, 100)],
    )
    candidate = Candidate(ticker="000000", price=10000)

    result = calc_expected_return(candidate, snapshot, sell_tick_offset=1)

    assert result.expect_price == 10000
    assert calc_target_sell_price(result.expect_price, 1) == 9950
    assert result.expect_revenue_percent == -0.5
