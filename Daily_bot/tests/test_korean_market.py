import csv
import json
from dataclasses import replace
from datetime import datetime, timedelta
from zipfile import ZipFile
import numpy as np
import pytest
from Daily_bot.backtest.calibrate_volatility import measure
from Daily_bot.backtest.fake_market import korean_config, generate_market, floor_price
from Daily_bot.backtest.regenerate_korean_mixed import reference_scenario


def test_recorded_returns_match_forward_horizon_and_deduplicate_quote_clock(tmp_path):
    path = tmp_path/'prices.csv'
    with path.open('w') as stream:
        writer = csv.DictWriter(stream,fieldnames=['session_date','ticker','scan_cycle_at','current_price','raw_json'])
        writer.writeheader()
        for second,price in [(0,100),(59,200),(59,200),(73,300),(120,400)]:
            writer.writerow(dict(session_date='2026-01-01',ticker='A',
                scan_cycle_at=(datetime(2026,1,1,9)+timedelta(seconds=second)).isoformat(),
                current_price=price,raw_json='{}'))
    result = measure([path],horizons=(60,))
    assert result['duplicate_quote_clocks']==1
    # 59 seconds is before the horizon and 73 is outside the 12s tolerance.
    # Only 59->120 seconds qualifies; never 0->59 or 0->73.
    assert result['horizons'][0]['observations']==1
    assert result['horizons'][0]['flat_percent']==0


def test_korean_session_end_archive_and_execution_invariants(tmp_path):
    cfg = korean_config(stocks=5,ticks_per_day=13,start_date='0001-01-01')
    out = generate_market(tmp_path/'run',cfg,viewer=False,archive_months=True)
    assert not list(out.glob('*.csv'))
    manifest = json.loads((out/'manifest.json').read_text())
    file = manifest['files'][0]
    with ZipFile(out/file['archive']) as archive:
        rows=list(csv.DictReader(archive.read(file['path']).decode('utf-8-sig').splitlines()))
    assert rows[0]['created_at']=='0001-01-01 09:00:00.000000'
    assert rows[-1]['created_at']=='0001-01-01 15:30:00.000000'
    last = {}
    for row in rows:
        p=int(row['current_price']);previous=int(row['prev_close_price']);raw=json.loads(row['raw_json'])
        assert .70*previous<=p<=1.30*previous and floor_price(p)==p
        if raw['executions']:
            assert p==raw['executions'][-1]['price']
        elif row['ticker'] in last:
            assert p==last[row['ticker']]
        last[row['ticker']]=p
    invalid = {str(cfg.start_date):{'bad': [1]*cfg.ticks_per_day}}
    with pytest.raises(ValueError,match='missing reference paths'):
        generate_market(tmp_path/'invalid',cfg,viewer=False,reference_paths=invalid)


def test_mixed_reference_continuity_endpoints_and_fixed_volatility_scaling():
    cfg=korean_config(seed=20261128,start_date='0001-01-01',days=130,ticks_per_day=25)
    paths,scenario=reference_scenario(cfg)
    assert len(paths)==130
    days=sorted(paths)
    for a,b in zip(days,days[1:]):
        for ticker in paths[a]:
            assert paths[a][ticker][-1]==paths[b][ticker][0]
    first=np.array([v[0] for v in paths[days[0]].values()])
    march=np.array([v[-1] for v in paths['0001-03-30'].values()])
    june=np.array([v[-1] for v in paths[days[-1]].values()])
    assert np.mean(march/first)==pytest.approx(1.0003**65)
    assert np.mean(june/march)==pytest.approx(.994**65)
    assert scenario['regimes'][1]['volatility_multiplier']==1.5
    quiet,_=reference_scenario(replace(cfg,n=0))
    for ticker in paths[days[0]]:
        assert np.std(np.diff(np.log(paths[days[0]][ticker])))>np.std(np.diff(np.log(quiet[days[0]][ticker])))


@pytest.mark.parametrize('patch', [dict(session_seconds=0),dict(session_seconds=23401),
                                   dict(ticks_per_day=2.5),dict(include_session_end=1)])
def test_bad_session_cadence_rejected(patch):
    with pytest.raises(ValueError):
        replace(korean_config(),**patch).validate()
