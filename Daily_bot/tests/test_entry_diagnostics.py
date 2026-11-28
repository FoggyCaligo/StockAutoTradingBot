import csv
import json
import pytest
from datetime import datetime, timedelta

from Daily_bot.backtest.diagnose_no_trades import diagnose, fit_depth_calibration
from Daily_bot.backtest.check_signal_realism import observations


def trace(day, ticker='ABC', price=1000, bid_volume=10000):
    raw={}
    for side,prefix,start,step,volume in [('bid','buy',999,-1,bid_volume),('ask','sel',1020,1,1)]:
        for i in range(1,11):
            key=f'{prefix}_fpr' if i==1 else f'{prefix}_{i}th_pre'
            raw[key+'_bid']=start+(i-1)*step;raw[key+'_req']=volume
    return dict(session_date=day,ticker=ticker,phase='scan_candidate',
        scan_cycle_at=f'{day} 09:30:00',created_at=f'{day} 09:30:00',
        current_price=price,raw_json=json.dumps(raw))


def test_diagnostics_distinguish_threshold_budget_and_stateful_unknown(tmp_path):
    rows=[trace('0001-01-01',bid_volume=1),trace('0001-01-02'),trace('0001-01-03')]
    # Tight symmetric book is a genuine no-signal day.
    raw=json.loads(rows[0]['raw_json'])
    for key in raw:
        if key.startswith('sel') and key.endswith('_bid'):raw[key]-=20
    rows[0]['raw_json']=json.dumps(raw)
    path=tmp_path/'market_traces_00010101.csv'
    with path.open('w',newline='')as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    result,_=diagnose([path],slot_budget=1000,observed_trades=[{'session_date':'0001-01-03'}])
    assert result['per_session']['0001-01-01']['classification']=='no_qualifying_signal'
    assert result['per_session']['0001-01-02']['classification']=='requires_stateful_replay_investigation'
    assert result['per_session']['0001-01-03']['classification']=='signal_available'
    result,_=diagnose([path],slot_budget=999)
    assert result['per_session']['0001-01-02']['classification']=='no_affordable_signal'


def test_calibration_does_not_fit_direction_or_use_future_profit():
    samples=[dict(session_date='2026-09-10',ticker=f'S{i}',log_depth_ratio=1+(i%10)/5)
             for i in range(100)]
    calibrated=fit_depth_calibration(samples,['test'])
    assert calibrated['training_samples']+calibrated['holdout_samples']==100
    assert calibrated['removed_training_median_log_ratio']>1
    assert 0<calibrated['depth_imbalance_sigma']<1
    assert calibrated['depth_imbalance_persistence']==.995
    assert 'target_return' not in calibrated


def test_signal_realism_supports_year_one_without_os_local_timestamps():
    t=datetime(1,1,1,9,30)
    rows=observations({('0001-01-01','A'):{
        t:(100,100,1,.2,True),t+timedelta(seconds=10):(101,101,None,None,False)}},horizons=(10,))
    assert rows[0]['actual_seconds']==10
    assert rows[0]['timestamp'].startswith('0001-')


def test_missing_quotes_are_not_mislabeled_as_a_strategy_no_signal_day(tmp_path):
    row=trace('0001-01-01');row['raw_json']='{}'
    path=tmp_path/'market_traces_00010101.csv'
    with path.open('w',newline='')as fp:
        w=csv.DictWriter(fp,fieldnames=list(row));w.writeheader();w.writerow(row)
    result,_=diagnose([path])
    assert result['per_session']['0001-01-01']['classification']=='invalid_or_missing_book'


def test_empty_input_and_invalid_threshold_fail_explicitly(tmp_path):
    path=tmp_path/'market_traces_empty.csv';path.write_text('session_date,phase\n')
    with pytest.raises(ValueError,match='No market trace rows'):diagnose([path])
    with pytest.raises(ValueError,match='threshold'):diagnose([path],threshold=float('nan'))
