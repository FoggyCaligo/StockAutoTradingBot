import argparse
import csv
import json
import random
from dataclasses import replace

import pytest

from Daily_bot.backtest.fake_market import (
    MarketConfig, OrderBook, add_market_arguments, config_from_args,
    advance_book, ordinary_bull_config, generate_market, adjacent,
)


def test_normal_cli_default_and_explicit_overrides():
    p=argparse.ArgumentParser();add_market_arguments(p)
    cfg=config_from_args(p.parse_args([]))
    assert cfg==ordinary_bull_config()
    assert config_from_args(p.parse_args(['--fake-profile','legacy']))==MarketConfig()
    custom=config_from_args(p.parse_args(['--fake-x','-12','--fake-n','0','--fake-market-order-probability','0']))
    assert (custom.x,custom.n,custom.market_order_probability)==(-12,0,0)
    assert custom.max_market_order_levels==1


def test_flat_valuation_has_no_mechanical_price_ratchet():
    book=OrderBook(10000);rng=random.Random(42)
    prices=[]
    cfg=ordinary_bull_config(x=0,n=0)
    for _ in range(2000):
        advance_book(book,10000,rng,cfg);prices.append(book.last_price)
    assert min(prices)>=10000
    assert max(prices)<=adjacent(10000,1)


def test_aggressive_orders_are_capped_to_one_resting_price_level():
    book=OrderBook(10000);rng=random.Random(17)
    cfg=ordinary_bull_config(market_order_probability=1,market_order_scale=10000)
    for _ in range(50):
        fills=advance_book(book,10000,rng,cfg)
        for side in ['buy','sell']:
            assert len({f.price for f in fills if f.aggressor_side==side})<=1


def test_no_aggressive_arrivals_leave_a_constant_valuation_untraded():
    book=OrderBook(10000);rng=random.Random(3)
    cfg=ordinary_bull_config(market_order_probability=0)
    for _ in range(50):assert advance_book(book,10000,rng,cfg)==[]
    assert book.last_price==10000


def test_reference_draws_are_independent_of_execution_arrivals(tmp_path):
    refs=[]
    for p in [0,1]:
        cfg=ordinary_bull_config(stocks=5,ticks_per_day=20,market_order_probability=p)
        out=generate_market(tmp_path/str(p),cfg,viewer=False)
        with next(out.glob('*.csv')).open(encoding='utf-8-sig') as f:
            refs.append([(r['ticker'],json.loads(r['raw_json'])['reference_price']) for r in csv.DictReader(f)])
    assert refs[0]==refs[1]


def test_unselected_stocks_keep_trading_between_recorded_days(tmp_path):
    cfg=ordinary_bull_config(seed=42,stocks=5,days=3,ticks_per_day=30)
    out=generate_market(tmp_path/'all-stocks',cfg,viewer=False)
    files=json.loads((out/'manifest.json').read_text())['files']
    for previous,current in zip(files,files[1:]):
        assert len(previous['closing_price_by_ticker'])==10
        assert current['previous_close_by_ticker']==previous['closing_price_by_ticker']
    assert all(item['rows']==150 for item in files)


@pytest.mark.parametrize('patch',[{'market_order_probability':-.1},{'market_order_probability':1.1},
    {'max_market_order_levels':-1},{'max_market_order_levels':.5},{'profile':'unknown'}])
def test_profile_configuration_rejects_invalid_flow_controls(patch):
    with pytest.raises(ValueError):replace(ordinary_bull_config(),**patch).validate()
