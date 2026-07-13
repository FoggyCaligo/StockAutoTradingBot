from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot
from Daily_bot.strategy.orderbook_predictor import (
    apply_orderbook_decay,
    calc_ask_depth_amount,
    calc_target_sell_price,
    predict_price_from_hoga,
)
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
    assert result.ask_depth_5_amount_krw == 10050 * 100 + 10100 * 100


def test_calc_ask_depth_amount_sums_top_five_ask_levels():
    snapshot = HogaSnapshot(
        ticker="000000",
        current_price=10000,
        asks=[
            HogaLevel(10010, 1),
            HogaLevel(10020, 2),
            HogaLevel(10030, 3),
            HogaLevel(10040, 4),
            HogaLevel(10050, 5),
            HogaLevel(10060, 100),
        ],
    )

    assert calc_ask_depth_amount(snapshot, levels=5) == (10010 * 1 + 10020 * 2 + 10030 * 3 + 10040 * 4 + 10050 * 5)


def test_apply_orderbook_decay_scales_far_levels_more_than_near_levels():
    snapshot = HogaSnapshot(
        ticker="000000",
        current_price=10000,
        bids=[HogaLevel(9990, 100), HogaLevel(9980, 100), HogaLevel(9970, 100)],
        asks=[HogaLevel(10010, 100), HogaLevel(10020, 100), HogaLevel(10030, 100)],
    )

    adjusted = apply_orderbook_decay(snapshot, bid_min_weight=0.1, ask_min_weight=0.1)

    assert [level.volume for level in adjusted.bids] == [100, 55, 10]
    assert [level.volume for level in adjusted.asks] == [100, 55, 10]


def test_calc_expected_return_uses_strategy_orderbook_decay():
    snapshot = HogaSnapshot(
        ticker="000000",
        current_price=10000,
        bids=[HogaLevel(9990, 1000), HogaLevel(9980, 1000), HogaLevel(9970, 1000)],
        asks=[HogaLevel(10010, 100), HogaLevel(10020, 100), HogaLevel(10030, 1000)],
    )
    candidate = Candidate(ticker="000000", price=10000)

    baseline = calc_expected_return(Candidate(ticker="000000", price=10000), snapshot, sell_tick_offset=1)
    adjusted = calc_expected_return(
        candidate,
        snapshot,
        sell_tick_offset=1,
        strategy_cfg={
            "orderbook_bid_linear_decay_min_weight": 0.1,
            "orderbook_ask_linear_decay_min_weight": 0.1,
        },
    )

    assert adjusted.ask_depth_5_amount_krw < baseline.ask_depth_5_amount_krw
