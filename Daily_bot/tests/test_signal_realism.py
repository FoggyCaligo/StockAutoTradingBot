from datetime import datetime, timedelta

from Daily_bot.backtest.check_signal_realism import observations


def test_future_match_is_forward_only_and_rejects_stale_endpoint():
    t=datetime(2026,11,24,9,30)
    data={t:(100,100,1,.2,True),t+timedelta(seconds=9):(110,110,None,None,False),
          t+timedelta(seconds=14):(120,120,None,None,False)}
    assert observations({('2026-11-24','A'):data},horizons=(10,),tolerance=3)==[]
    rows=observations({('2026-11-24','A'):data},horizons=(10,),tolerance=4)
    assert rows[0]['actual_seconds']==14
    assert round(rows[0]['return_percent'],8)==20


def test_watch_observations_never_become_entry_anchors():
    t=datetime(2026,11,24,9,30)
    data={t:(100,100,1,.2,False),t+timedelta(seconds=10):(110,110,1,.2,False)}
    assert observations({('2026-11-24','A'):data},horizons=(10,))==[]


def test_no_cross_symbol_or_cross_day_future_pairing():
    t=datetime(2026,11,24,9,30)
    groups={('2026-11-24','A'):{t:(100,100,1,.2,True)},
            ('2026-11-24','B'):{t+timedelta(seconds=10):(110,110,1,.2,False)}}
    assert observations(groups,horizons=(10,))==[]
