from dataclasses import replace
from datetime import datetime, timedelta
import inspect
import json

import pytest

from Daily_bot.backtest import replay_market_traces as base
from Daily_bot.backtest import replay_dynamic_expect_debounce as dynamic
from Daily_bot.backtest.execution_replay import ExecutionConfig, SnapshotMatcher
from Daily_bot.backtest.replay_db_builder import build_replay_db_from_logs
from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot, OrderResult
from Daily_bot.storage.db import Recorder
from Daily_bot.telemetry.trace_helpers import trace_buy_order_quote


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


def test_signal_snapshot_only_fills_crossing_buys_within_shared_depth():
    matcher = SnapshotMatcher(ExecutionConfig(
        depth_participation=0.5, allow_signal_snapshot_buy=True
    ))
    frame = matcher.frame(row(0, ask=1000, aq=10))
    passive = matcher.submit("AAA", "buy", 10, 999, START, "entry")
    first = matcher.submit("AAA", "buy", 3, 1000, START, "entry")
    second = matcher.submit("AAA", "buy", 10, 1000, START, "entry")
    sell = matcher.submit("AAA", "sell", 10, 999, START, "take_profit")
    assert matcher.match(passive, frame) == []
    assert matcher.match(sell, frame) == []
    assert matcher.match(first, frame) == [(3, 1000, "signal_snapshot_depth")]
    assert matcher.match(second, frame) == [(2, 1000, "signal_snapshot_depth")]
    assert not second.joined
    assert matcher.match(second, matcher.frame(row(1, ask=1000, aq=10))) == []


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
    assert result[0].exit_time.endswith('09:30:31')
    assert next(e['time'] for e in result.execution_events if e['event'] == 'cancel').endswith('09:30:30')
    assert not result.unresolved_inventory
    assert result.execution_sessions[0]['ending_cash']==pytest.approx(10035.722)


def test_full_buy_fill_waits_for_next_broker_poll_before_target_order(monkeypatch):
    traces = [row(0), row(1, ask=1000, aq=100), row(2, bid=1020), row(3, bid=1020)]
    monkeypatch.setattr(base, 'load_traces', lambda _: traces)
    monkeypatch.setattr(base, 'apply_orderbook_level_limit', lambda rows, **kwargs: rows)

    result = dynamic.run_backtest_dynamic_expect_debounce(**arguments())

    assert result[0].entry_time.endswith('09:30:01')
    assert result[0].exit_time.endswith('09:30:03')
    confirmation = next(event for event in result.execution_events if event['event'] == 'confirm')
    assert confirmation['time'].endswith('09:30:02')


def test_recorded_order_quotes_replay_from_sqlite_and_csv(tmp_path):
    submitted_at = datetime.now().replace(hour=9, minute=32, second=0, microsecond=250000)
    order = OrderResult('BROKER-1', 'AAA', 'BUY', 4, 1000)
    candidate = Candidate(ticker='AAA', price=1000, expect_price=1020, prev_close_price=990)
    recorder = Recorder(tmp_path / 'live.sqlite3')

    class QuoteClient:
        def get_20hoga(self, ticker, *, best_effort):
            assert ticker == order.ticker and best_effort
            return snapshot

    for delay, bid, ask in ((0.9, 999, 1000), (1.1, 1001, 1002), (2.1, 999, 1000),
                            (5, 1020, 1021)):
        snapshot = HogaSnapshot(
            'AAA', ask, [HogaLevel(bid, 100)], [HogaLevel(ask, 100)],
            submitted_at + timedelta(seconds=delay),
            dict(cur_prc=ask, buy_fpr_bid=bid, buy_fpr_req=100, sel_fpr_bid=ask, sel_fpr_req=100),
        )
        trace_buy_order_quote(QuoteClient(), recorder, candidate, order, submitted_at, {})
    assert (candidate.price, candidate.expect_price) == (1000, 1020)
    recorder.conn.close()

    rebuilt = build_replay_db_from_logs(recorder.log_dir, tmp_path / 'rebuilt.sqlite3')
    assert base.load_traces(rebuilt) == base.load_traces(recorder.path)
    values = arguments()
    values.update(db_path=rebuilt, execution_config=ExecutionConfig(replay_recorded_buy_orders=True))

    result = dynamic.run_backtest_dynamic_expect_debounce(**values)

    assert len(result) == 1
    assert result[0].entry_time.endswith('09:32:02.350000')
    assert result[0].quantity == 4
    assert result.execution_events[0]['broker_order_id'] == 'BROKER-1'
    assert result.execution_events[0]['time'] == submitted_at.isoformat()


def test_runner_exits_signal_snapshot_buy_on_later_quote(monkeypatch):
    traces = [row(0, ask=1000), row(1, bid=1020, ask=1021)]
    monkeypatch.setattr(base, "load_traces", lambda _: traces)
    monkeypatch.setattr(base, "apply_orderbook_level_limit", lambda rows, **kwargs: rows)
    values = arguments()
    values["execution_config"] = ExecutionConfig(
        depth_participation=0.5, allow_signal_snapshot_buy=True
    )
    result = dynamic.run_backtest_dynamic_expect_debounce(**values)
    assert len(result) == 1
    assert result[0].entry_time.endswith("09:30:00")
    assert result[0].exit_time.endswith("09:30:01")


def test_runner_does_not_fabricate_end_of_data_liquidation(monkeypatch):
    monkeypatch.setattr(base,'load_traces',lambda _:[row(0),row(1,ask=1000,aq=100),row(2)])
    monkeypatch.setattr(base,'apply_orderbook_level_limit',lambda rows,**kwargs:rows)
    result=dynamic.run_backtest_dynamic_expect_debounce(**arguments())
    assert result==[]
    assert result.unresolved_inventory[0]['quantity']==9
    assert result.unresolved_inventory[0]['mark_only'] is True


def test_default_standard_can_replay_sparse_quotes_without_post_order_samples(monkeypatch):
    import sys

    traces = [row(0), row(60, price=1020, bid=1019, ask=1021)]
    monkeypatch.setattr(base, 'load_traces', lambda _: traces)
    monkeypatch.setattr(base, 'apply_orderbook_level_limit', lambda rows, **kwargs: rows)
    monkeypatch.setattr(sys, 'argv', ['replay_market_traces.py'])
    values = arguments()
    values['execution_config'] = base.parse_args().execution_config

    standard = dynamic.run_backtest_dynamic_expect_debounce(**values)

    assert len(standard) == 1
    assert standard[0].entry_time.endswith('09:30:00')
    assert standard[0].exit_time.endswith('09:31:00')
    assert standard[0].entry_price == 1000
    assert standard[0].exit_reason == 'take_profit'
    values['execution_config'] = ExecutionConfig()
    conservative = dynamic.run_backtest_dynamic_expect_debounce(**values)
    assert not conservative
    assert any(event['reason'] == 'buy_timeout' for event in conservative.execution_events)


def test_net_summary_treats_small_gross_gain_as_loss_after_costs(capsys):
    trade = base.BacktestTrade('2026-11-24', 'AAA', '2026-11-24 09:30:00',
        '2026-11-24 09:31:00', 1, 1000, 1001, 1000, 1001, 'take_profit', .1)

    base.print_summary([trade])

    output = capsys.readouterr().out
    assert 'net_profit_krw=-1.10' in output
    assert 'net_wins=0 net_losses=1 net_win_rate=0.00%' in output


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
