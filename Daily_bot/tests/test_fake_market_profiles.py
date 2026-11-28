import argparse
import csv
import json
import random
from dataclasses import replace

import pytest

from Daily_bot.backtest.fake_market import (
    MarketConfig, OrderBook, add_market_arguments, config_from_args,
    advance_book, ordinary_bull_config, korean_config, generate_market, adjacent,
)


def test_normal_cli_default_and_explicit_overrides():
    p=argparse.ArgumentParser();add_market_arguments(p)
    cfg=config_from_args(p.parse_args([]))
    assert cfg==korean_config()
    assert config_from_args(p.parse_args(["--fake-profile","ordinary-bull"]))==ordinary_bull_config()
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


def test_depth_calibration_is_opt_in_and_explicit_flags_override_file(tmp_path):
    path=tmp_path/'depth.json'
    path.write_text(json.dumps(dict(schema_version=1,depth_imbalance_sigma=.9,depth_imbalance_persistence=.995)))
    p=argparse.ArgumentParser();add_market_arguments(p)
    cfg=config_from_args(p.parse_args(['--fake-depth-calibration',str(path)]))
    assert cfg.depth_imbalance_sigma==.9
    cfg=config_from_args(p.parse_args(['--fake-depth-calibration',str(path),'--fake-depth-imbalance-sigma','0']))
    assert cfg.depth_imbalance_sigma==0


def test_depth_shocks_do_not_change_exogenous_reference_draws(tmp_path):
    refs=[]
    for sigma in [0,.9]:
        cfg=ordinary_bull_config(stocks=5,ticks_per_day=30,depth_imbalance_sigma=sigma)
        out=generate_market(tmp_path/str(sigma),cfg,viewer=False)
        with next(out.glob('*.csv')).open(encoding='utf-8-sig')as fp:
            refs.append([(r['ticker'],json.loads(r['raw_json'])['reference_price'])for r in csv.DictReader(fp)])
    assert refs[0]==refs[1]


def test_depth_shocks_do_not_create_an_upward_ratchet_at_flat_reference():
    book=OrderBook(10000);rng=random.Random(123)
    cfg=ordinary_bull_config(x=0,n=0,depth_imbalance_sigma=1.)
    prices=[]
    for _ in range(1000):
        advance_book(book,10000,rng,cfg);prices.append(book.last_price)
    assert min(prices)>=10000 and max(prices)<=10010


def test_twenty_stock_year_one_run_keeps_all_previous_closes(tmp_path):
    cfg=ordinary_bull_config(stocks=20,days=2,start_date='0001-01-01',ticks_per_day=20,depth_imbalance_sigma=.9)
    out=generate_market(tmp_path/'twenty',cfg,viewer=False)
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['model_version']==3
    assert [f['path']for f in manifest['files']]==['fake_market_traces_00010101.csv','fake_market_traces_00010102.csv']
    assert all(len(f['tickers'])==20 and f['rows']==400 for f in manifest['files'])
    assert manifest['files'][1]['previous_close_by_ticker']==manifest['files'][0]['closing_price_by_ticker']


@pytest.mark.parametrize('patch',[dict(depth_imbalance_sigma=float('nan')),dict(depth_imbalance_sigma=-1),
    dict(depth_imbalance_sigma=4),dict(depth_imbalance_persistence=1),dict(depth_imbalance_persistence=-.1),
    dict(profile='legacy',depth_imbalance_sigma=1)])
def test_invalid_depth_shock_controls_fail_before_generation(patch):
    with pytest.raises(ValueError):replace(ordinary_bull_config(),**patch).validate()


def test_calibration_price_samples_are_used_without_strategy_threshold_fitting(tmp_path):
    cfg=ordinary_bull_config(stocks=5,ticks_per_day=2,initial_price_samples=(13220,))
    out=generate_market(tmp_path/'prices',cfg,viewer=False)
    manifest=json.loads((out/'manifest.json').read_text())
    assert set(manifest['files'][0]['previous_close_by_ticker'].values())=={13220}
    assert manifest['model_version']==3


@pytest.mark.parametrize('prices',[(0,),(-1,),('1000',),(1000.,)])
def test_invalid_price_calibration_is_rejected(prices):
    with pytest.raises(ValueError):replace(ordinary_bull_config(),initial_price_samples=prices).validate()


