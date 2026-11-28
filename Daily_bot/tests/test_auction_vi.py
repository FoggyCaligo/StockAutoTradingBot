import pytest
from Daily_bot.backtest.fake_market import OrderBook
from Daily_bot.backtest.auction import AuctionRules


def auction(book):
    book.auction=AuctionRules(book,jitter=0)
    return book.auction


def test_closing_collection_single_price_volume_and_fifo():
    b=OrderBook(10000);a=auction(b);a.begin_tick(22800)
    b.submit('buy',5,10100);b.submit('buy',4,10050)
    b.submit('sell',3,9950);first=b.sequence;b.submit('sell',8,10050)
    assert b.last_price==10000 and a.mode=='closing_auction'
    assert a.finish_tick()==[]
    a.begin_tick(23400);fills=a.finish_tick()
    assert sum(f.quantity for f in fills)==9
    assert {f.price for f in fills}=={10050}
    assert fills[0].resting_sequence==first and fills[0].quantity==3
    assert b.last_price==10050 and b.snapshot('sell')==[[10050,2]]
    assert a.mode=='closed'


@pytest.mark.parametrize('kospi200,price',[(False,10600),(True,10300)])
def test_dynamic_vi_intercepts_before_execution_and_waits_two_minutes(kospi200,price):
    b=OrderBook(10000);a=AuctionRules(b,kospi200=kospi200,jitter=0);b.auction=a
    a.begin_tick(1800);b.submit('sell',10,price)
    assert b.submit('buy',5,price)==[]
    assert b.last_price==10000 and a.mode=='vi_auction' and a.until==1920
    a.begin_tick(1910);assert a.finish_tick()==[]
    a.begin_tick(1920);fills=a.finish_tick()
    assert sum(f.quantity for f in fills)==5 and {f.price for f in fills}=={price}
    assert a.static_reference==price and a.mode=='continuous'


def test_static_vi_and_no_nested_vi_during_collection():
    b=OrderBook(10000);a=auction(b);b.last_price=10900
    a.begin_tick(2000);b.submit('sell',10,11000);b.submit('buy',5,11000)
    assert a.events[-1]['kind']=='static'
    b.submit('buy',10,11500);b.submit('sell',5,11400)
    assert len([e for e in a.events if e['event']=='vi_start'])==1


def test_closing_vi_extends_and_market_residual_cannot_set_price():
    b=OrderBook(10000);a=auction(b);a.begin_tick(23390)
    b.submit('buy',50,10500);b.submit('sell',10,10500)
    assert a.finish_tick()==[] and a.until==23510
    a.begin_tick(23500);assert a.finish_tick()==[]
    a.begin_tick(23510);fills=a.finish_tick()
    assert sum(f.quantity for f in fills)==10 and b.last_price==10500
    assert a.mode=='closed'
    empty=OrderBook(10000);e=auction(empty);e.begin_tick(22800)
    empty.submit('buy',100);e.begin_tick(23400)
    assert e.finish_tick()==[] and empty.last_price==10000


def test_symmetric_uniform_price_fills_conserve_quantities():
    b=OrderBook(10000);a=auction(b);a.begin_tick(22800)
    b.submit('buy',7);b.submit('buy',3,10010)
    b.submit('sell',5);b.submit('sell',20,10000)
    a.begin_tick(23400);fills=a.finish_tick()
    assert sum(f.quantity for f in fills)==10
    assert len({f.price for f in fills})==1
    assert sum(q for p,q in b.snapshot('sell'))==15


def test_replay_does_not_fill_during_collection_and_uncrosses_at_single_price():
    import json
    from dataclasses import replace
    from Daily_bot.tests.test_execution_replay import row,START
    from Daily_bot.backtest.execution_replay import SnapshotMatcher,ExecutionConfig
    from Daily_bot.backtest import replay_market_traces as base
    m=SnapshotMatcher(ExecutionConfig(depth_participation=.5,queue_multiplier=0))
    order=m.submit('AAA','buy',10,1010,START,'entry')
    collecting=replace(row(10,ask=1001),raw_json=json.dumps(dict(market_state='vi_auction',sel_fpr_bid=1001,sel_fpr_req=100)))
    assert not base.can_execute(collecting)
    assert m.match(order,m.frame(collecting))==[]
    clearing=replace(row(130),raw_json=json.dumps(dict(market_state='auction_uncross',auction_price=1005,auction_volume=12)))
    assert base.can_execute(clearing) and not base.can_enter(clearing)
    assert m.match(order,m.frame(clearing))==[(6,1005,'auction_volume')]
    assert m.match(order,m.frame(clearing))==[]


def test_daily_archive_adapter_matches_sqlite_import_and_standard_runner(tmp_path):
    from pathlib import Path
    from Daily_bot.backtest.fake_market import korean_config,generate_market
    from Daily_bot.backtest.replay_db_builder import build_replay_db_from_logs
    from Daily_bot.backtest import replay_market_traces as base
    from Daily_bot.backtest.replay_synthetic_archives import load_csv_bytes,replay_day
    folder=generate_market(tmp_path/'tape',korean_config(ticks_per_day=15),viewer=False)
    csvfile=next(folder.glob('*.csv'))
    db=build_replay_db_from_logs(folder,tmp_path/'replay.sqlite3',market_pattern='fake_market_traces_*.csv')
    loaded=base.load_traces(db)
    adapted=load_csv_bytes(csvfile.read_bytes())
    assert loaded==adapted
    assert replay_day(loaded)==replay_day(adapted)


def test_standard_risk_intent_waits_for_vi_uncross_without_changing_stop_rule(monkeypatch):
    import json
    from dataclasses import replace
    from Daily_bot.tests.test_execution_replay import row
    from Daily_bot.backtest import replay_market_traces as base
    from Daily_bot.backtest.replay_synthetic_archives import replay_day
    monkeypatch.setattr(base,'apply_orderbook_level_limit',lambda rows,**kwargs:rows)
    entry=row(0)
    frozen=[replace(row(s),expect_price=999,raw_json=json.dumps({'market_state':'vi_auction'})) for s in (10,20,30)]
    release=replace(row(130,price=1015),expect_price=1100,
        raw_json=json.dumps({'market_state':'auction_uncross','auction_price':1015,'auction_volume':1000}))
    result=replay_day([entry,*frozen,release])
    assert len(result)==1
    assert result[0].exit_reason=='dynamic_expect_stop'
    assert result[0].exit_price==1015
    assert result[0].exit_time==release.created_at


def test_auction_requires_a_timeline_that_can_resolve_two_minute_interruptions():
    from Daily_bot.backtest.fake_market import korean_config
    with pytest.raises(ValueError,match='10s or finer'):
        korean_config(auction_vi=True,ticks_per_day=15).validate()


def test_2026_tax_repricing_keeps_standard_ledger_and_original_report_unchanged():
    from Daily_bot.backtest.replay_synthetic_archives import reprice_standard_report
    old=dict(fill_model='standard',sell_tax_rate=.0018,fee_rate_per_side=.00015,capital_basis_krw=1000000,
        summaries={'all':{'unfilled_mark_positions':0}},days=[{'date':'0001-01-01','trades':1,'gross_pnl_krw':1000,'net_pnl_krw':788.05,'cost_krw':211.95}],
        trades=[{'session_date':'0001-01-01','buy_amount_krw':100000,'sell_amount_krw':101000,'gross_pnl_krw':1000,'net_pnl_krw':788.05,'cost_krw':211.95,'exit_reason':'take_profit'}])
    new=reprice_standard_report(old)
    assert new['summaries']['all']['net_profit_krw']==pytest.approx(767.85)
    assert new['trades'][0]['sell_amount_krw']==old['trades'][0]['sell_amount_krw']
    assert old['sell_tax_rate']==.0018 and old['trades'][0]['net_pnl_krw']==788.05
    with pytest.raises(ValueError):reprice_standard_report(dict(old,fill_model='conservative'))
