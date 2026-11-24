from dataclasses import replace
from datetime import datetime, timedelta
import inspect
import json

import pytest

from Daily_bot.backtest import replay_market_traces as base
from Daily_bot.backtest import replay_dynamic_expect_debounce as dynamic
from Daily_bot.backtest.execution_replay import ExecutionConfig, SnapshotMatcher


START = datetime(2026, 11, 24, 9, 30)


def row(second, *, bid=999, ask=1001, bq=100, aq=100, price=1000, executions=None, volume=0):
    raw=dict(buy_fpr_bid=bid,buy_fpr_req=bq,sel_fpr_bid=ask,sel_fpr_req=aq,
             tick_volume=volume,executions=executions or [])
    return base.TraceRow('2026-11-24','AAA',(START+timedelta(seconds=second)).isoformat(sep=' '),
        'scan_candidate' if second==0 else 'watch',0,price,1000,price,1011,1.0,.2,100000,0,json.dumps(raw))


def test_latency_partial_depth_and_no_reuse_of_unchanged_quote():
    m=SnapshotMatcher(ExecutionConfig(latency_seconds=2,depth_participation=.5))
    m.frame(row(0))
    o=m.submit('AAA','buy',20,1001,START,'entry')
    assert m.match(o,m.frame(row(0)))==[]
    assert m.match(o,m.frame(row(1)))==[]
    assert m.match(o,m.frame(row(2,aq=10)))==[(5,1001,'displayed_depth')]
    assert o.remaining==15
    assert m.match(o,m.frame(row(2,aq=10)))==[]
    assert m.match(o,m.frame(row(3,aq=10)))==[]
    assert m.match(o,m.frame(row(4,aq=16)))==[(3,1001,'displayed_depth')]


def test_fifo_requires_volume_cancellation_and_price_touch_do_not_fill():
    m=SnapshotMatcher(ExecutionConfig(depth_participation=1))
    o=m.submit('AAA','sell',5,1010,START,'take_profit')
    m.frame(row(0,ask=1010,aq=10))
    # Tape at arrival occurred before our queue position was observed.
    assert m.match(o,m.frame(row(1,ask=1010,aq=10,
        executions=[dict(price=1010,quantity=100,aggressor_side='buy')])))==[]
    assert o.queue_ahead==10
    assert m.match(o,m.frame(row(2,ask=1010,aq=1,price=1010)))==[]
    assert o.queue_ahead==10
    assert m.match(o,m.frame(row(3,ask=1010,aq=1,
        executions=[dict(price=1010,quantity=12,aggressor_side='buy')])))==[(2,1010,'execution')]
    assert o.remaining==3


def test_tape_cannot_cross_missing_interval_or_wrong_aggressor():
    m=SnapshotMatcher(ExecutionConfig(depth_participation=1))
    o=m.submit('AAA','sell',5,1010,START,'take_profit')
    m.frame(row(0,ask=1010,aq=1));m.match(o,m.frame(row(1,ask=1010,aq=1)))
    assert m.match(o,m.frame(row(2,ask=1010,executions=[dict(price=1010,quantity=100,aggressor_side='sell')])))==[]
    assert m.match(o,m.frame(row(60,ask=1010,executions=[dict(price=1010,quantity=100,aggressor_side='buy')])))==[]


def test_market_exit_walks_bid_depth_and_never_fills_all_at_last_price():
    m=SnapshotMatcher(ExecutionConfig(depth_participation=.1))
    o=m.submit('AAA','sell',50,None,START,'dynamic_expect_stop')
    frame=m.frame(row(1,bid=990,bq=20,price=1000))
    assert m.match(o,frame)==[(2,990,'displayed_depth')]
    assert o.remaining==48


def test_aggregate_volume_is_opt_in_and_requires_strict_trade_through():
    for enabled in [False,True]:
        m=SnapshotMatcher(ExecutionConfig(depth_participation=1,aggregate_trade_through=enabled))
        o=m.submit('AAA','sell',5,1010,START,'take_profit')
        m.frame(row(0,ask=1010,aq=1));m.match(o,m.frame(row(1,ask=1010,aq=1)))
        assert m.match(o,m.frame(row(2,price=1010,ask=1010,volume=20)))==[]
        fills=m.match(o,m.frame(row(3,price=1011,ask=1010,volume=20)))
        assert bool(fills)==enabled


def arguments():
    sig=inspect.signature(dynamic.run_backtest_dynamic_expect_debounce)
    values={name:p.default for name,p in sig.parameters.items() if p.default is not inspect.Parameter.empty}
    values.update(db_path=None,min_expected_return_percent=.71,max_spread_percent=0,
        top_n_per_day=1,stop_loss_percent=0,stop_loss_tick_multiplier=0,use_selected_signals=False,
        default_starting_capital_krw=10000,min_slot_count=1,max_slot_count=1,max_budget_per_stock_krw=10000,
        allow_refill_empty_slots=True,dynamic_expect_stop_threshold_percent=-10,
        execution_config=ExecutionConfig(depth_participation=.5))
    return values


def test_runner_tracks_partial_buy_timeout_then_delayed_sell(monkeypatch):
    traces=[row(0),row(1,ask=1000,aq=4),row(2,ask=1000,aq=4),
            row(31,bid=1020,ask=1021),row(32,bid=1020,ask=1021)]
    monkeypatch.setattr(base,'load_traces',lambda _:traces)
    monkeypatch.setattr(base,'apply_orderbook_level_limit',lambda rows,**kwargs:rows)
    result=dynamic.run_backtest_dynamic_expect_debounce(**arguments())
    assert len(result)==1
    assert result[0].quantity==2
    assert result[0].entry_price==1000
    assert result[0].exit_price==1020
    assert result[0].exit_time.endswith('09:30:32')
    assert not result.unresolved_inventory
    assert result.execution_sessions[0]['ending_cash']==pytest.approx(10035.722)


def test_runner_does_not_fabricate_end_of_data_liquidation(monkeypatch):
    monkeypatch.setattr(base,'load_traces',lambda _:[row(0),row(1,ask=1000,aq=100),row(2)])
    monkeypatch.setattr(base,'apply_orderbook_level_limit',lambda rows,**kwargs:rows)
    result=dynamic.run_backtest_dynamic_expect_debounce(**arguments())
    assert result==[]
    assert result.unresolved_inventory[0]['quantity']==9
    assert result.unresolved_inventory[0]['mark_only'] is True


def test_validation_and_actual_fill_overrides_are_rejected(monkeypatch):
    with pytest.raises(ValueError): ExecutionConfig(latency_seconds=-1)
    with pytest.raises(ValueError): ExecutionConfig(depth_participation=0)
    with pytest.raises(ValueError): ExecutionConfig(latency_seconds=float('nan'))
    values=arguments();values['actual_exit_overrides_by_ticker']={('x','y'):[object()]}
    with pytest.raises(ValueError,match='actual-fill'):
        dynamic.run_backtest_dynamic_expect_debounce(**values)


def test_slot_and_refill_wrappers_work_with_conservative_engine(monkeypatch, tmp_path):
    import sys
    from Daily_bot.backtest import replay_refill_threshold as runner
    traces=[row(0),row(1,ask=1000,aq=4),row(2,ask=1000,aq=4),
            row(31,bid=1020,ask=1021),row(32,bid=1020,ask=1021)]
    monkeypatch.setattr(base,'load_traces',lambda _:traces)
    monkeypatch.setattr(base,'apply_orderbook_level_limit',lambda rows,**kwargs:rows)
    outcomes=[]
    def run():
        outcomes.append(dynamic.run_backtest_dynamic_expect_debounce(**arguments()))
    monkeypatch.setattr(dynamic,'main',run)
    monkeypatch.setattr(sys,'argv',['replay_refill_threshold.py','--refill-min-expected-return','0.90'])
    original_trade=base.BacktestTrade
    runner.main()
    assert len(outcomes[0])==1
    assert outcomes[0][0].quantity==2
    assert base.BacktestTrade is original_trade


def test_base_runner_accepts_execution_config_and_emits_inventory_report(monkeypatch, tmp_path):
    from Daily_bot.backtest.execution_replay import write_execution_report
    monkeypatch.setattr(base,'load_traces',lambda _:[row(0),row(1,ask=1000,aq=100),row(2)])
    monkeypatch.setattr(base,'apply_orderbook_level_limit',lambda rows,**kwargs:rows)
    a=arguments()
    accepted=inspect.signature(base.run_backtest).parameters
    result=base.run_backtest(**{k:v for k,v in a.items() if k in accepted})
    write_execution_report(tmp_path/'result.csv',result)
    data=json.loads((tmp_path/'result_execution.json').read_text())
    assert data['unresolved_inventory'][0]['quantity']==9
    assert data['sessions'][0]['ending_cash']>=0
