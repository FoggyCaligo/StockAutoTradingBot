import pytest

from Daily_bot.utils import get_tick_size, round_to_tick, move_price_by_ticks
from Daily_bot.backtest.fake_market import tick_size
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price


@pytest.mark.parametrize('price,expected', [
    (999,1),(1000,1),(1999,1),(2000,5),(4999,5),(5000,10),
    (9990,10),(10000,10),(15000,10),(19999,10),(20000,50),
    (49999,50),(50000,100),(99900,100),(100000,100),(150000,100),
    (199999,100),(200000,500),(499999,500),(500000,1000),
])
def test_live_replay_and_generator_use_same_official_share_grid(price, expected):
    assert get_tick_size(price) == tick_size(price) == expected
    assert tick_size is get_tick_size  # one implementation, not two drifting copies


@pytest.mark.parametrize('boundary,below', [
    (2000,1999),(5000,4995),(20000,19990),
    (50000,49950),(200000,199900),(500000,499500),
])
def test_one_tick_exit_crosses_band_boundary_correctly(boundary, below):
    assert move_price_by_ticks(boundary,-1) == below
    assert int(calc_target_sell_price(boundary,1)) == below
    assert move_price_by_ticks(below,1) == boundary


def test_fifteen_thousand_price_is_not_rounded_in_fifty_won_steps():
    assert round_to_tick(15029) == 15020
    assert int(calc_target_sell_price(15020,1)) == 15010
