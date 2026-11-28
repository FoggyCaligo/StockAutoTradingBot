import argparse
import csv
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from Daily_bot.backtest.fake_market import (
    MarketConfig, OrderBook, add_market_arguments, adjacent, floor_price,
    generate_market, prepare_market_args, tick_size,
)
from Daily_bot.backtest.replay_db_builder import build_replay_db_from_logs
from Daily_bot.backtest.replay_market_traces import load_traces, apply_orderbook_level_limit


def test_price_priority_fifo_partial_fills_and_last_trade():
    book = OrderBook(10000)
    book.submit('sell', 3, 10020)
    book.submit('sell', 4, 10010)
    first = book.sequence
    book.submit('sell', 6, 10010)
    second = book.sequence
    fills = book.submit('buy', 7, 10020)
    assert [(f.price, f.quantity, f.resting_sequence) for f in fills] == [
        (10010, 4, first), (10010, 3, second)]
    assert book.snapshot('sell') == [[10010, 3], [10020, 3]]
    assert book.last_price == 10010
    book.submit('buy', 5, 9990)
    assert book.last_price == 10010  # unmatched bid cannot set last
    assert book.cancel('sell', 10010, 2) == 2
    assert book.snapshot('sell')[0] == [10010, 1]
    assert book.last_price == 10010


def test_sell_sweeps_highest_bid_and_market_residual_is_cancelled():
    book = OrderBook(10000)
    book.submit('buy', 2, 9990)
    book.submit('buy', 3, 10000)
    fills = book.submit('sell', 100)
    assert [(f.price, f.quantity) for f in fills] == [(10000, 3), (9990, 2)]
    assert book.last_price == 9990
    assert book.bids == book.asks == {}
    assert book.submit('buy', 5) == []
    assert book.last_price == 9990


@pytest.mark.parametrize('boundary,below,above', [
    (2000,1,5),(5000,5,10),(20000,10,50),(50000,50,100),
    (200000,100,500),(500000,500,1000),
])
def test_krx_grid_boundary(boundary, below, above):
    assert tick_size(boundary-1) == below
    assert tick_size(boundary) == above
    assert adjacent(boundary, -1) == boundary-below
    assert adjacent(boundary-below, 1) == boundary
    assert floor_price(boundary+above-1) == boundary


def test_invalid_orders_and_daily_limits():
    book = OrderBook(10000)
    for side, quantity, price in [('x',1,10000),('buy',0,10000),('buy',1,10001),
                                   ('buy',1,13010),('sell',1,6990)]:
        with pytest.raises(ValueError):
            book.submit(side, quantity, price)


def test_seed_reproduction_trace_integrity_and_fake_import(tmp_path):
    cfg = MarketConfig(seed=123, stocks=5, ticks_per_day=120, scan_interval_seconds=600)
    dirs = [generate_market(tmp_path / name, cfg, viewer=False) for name in ('a', 'b')]
    files = [next(p.glob('fake_market_traces_*.csv')) for p in dirs]
    assert files[0].read_bytes() == files[1].read_bytes()
    rows = list(csv.DictReader(files[0].open(encoding='utf-8-sig')))
    assert len(rows) == 600
    assert {r['phase'] for r in rows} == {'scan_candidate', 'watch'}
    last = {}
    for row in rows:
        raw = json.loads(row['raw_json'])
        ticker = row['ticker']
        price = int(row['current_price'])
        assert row['prev_close_price'] and raw['synthetic']
        assert floor_price(price) == price
        assert int(row['best_bid']) < int(row['best_ask']) or not int(row['best_ask']) or not int(row['best_bid'])
        for key, value in raw.items():
            if key.endswith('_req'):
                assert value > 0
        if raw['executions']:
            assert price == raw['executions'][-1]['price']
            assert raw['tick_volume'] == sum(f['quantity'] for f in raw['executions'])
        elif ticker in last:
            assert last[ticker] == price
        last[ticker] = price
    with pytest.raises(FileNotFoundError):  # real importer never discovers fake files by default
        build_replay_db_from_logs(dirs[0], tmp_path / 'real.sqlite3')
    db = build_replay_db_from_logs(dirs[0], tmp_path / 'fake.sqlite3', market_pattern='fake_market_traces_*.csv')
    loaded = load_traces(db)
    assert len(loaded) == len(rows)
    rebuilt = apply_orderbook_level_limit(loaded, 10, 1, 0.0, 0.0)
    assert len(rebuilt) == len(rows)
    assert rebuilt[0].expect_price == int(rows[0]['expect_price'])
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM fills').fetchone()[0] == 0
    with pytest.raises(FileExistsError):
        generate_market(dirs[0], cfg)


def test_direction_and_hour_tick_noise_are_reproducible(tmp_path):
    cfg = MarketConfig(seed=7, stocks=5, ticks_per_day=380, n=0, x=200)
    for sign in (1, -1):
        out = generate_market(tmp_path / str(sign), replace(cfg, x=sign*200), viewer=False)
        rows = list(csv.DictReader(next(out.glob('*.csv')).open(encoding='utf-8-sig')))
        for row in rows:
            elapsed = (float(row['created_at'][11:13])-9)*3600 + float(row['created_at'][14:16])*60 + float(row['created_at'][17:])
            expected = int(row['prev_close_price'])*(1+sign*200*elapsed/22800/10000)
            assert json.loads(row['raw_json'])['reference_price'] == pytest.approx(expected, abs=1e-6)


def test_multiple_days_skip_weekend_and_have_5_to_10_names(tmp_path):
    out = generate_market(tmp_path/'multi', MarketConfig(days=3, start_date='2026-11-27', ticks_per_day=2), viewer=False)
    manifest = json.loads((out/'manifest.json').read_text())
    assert [v['path'] for v in manifest['files']] == [
        'fake_market_traces_20261127.csv', 'fake_market_traces_20261130.csv', 'fake_market_traces_20261201.csv']
    assert all(5 <= len(v['tickers']) <= 10 for v in manifest['files'])


@pytest.mark.parametrize('patch', [dict(stocks=4),dict(days=0),dict(ticks_per_day=1),dict(n=-1),
                                    dict(a=10,b=1),dict(c=2,d=1),dict(x=float('nan')),
                                    dict(cancellation_probability=1.1),dict(scan_interval_seconds=0)])
def test_configuration_validation(patch):
    with pytest.raises(ValueError):
        replace(MarketConfig(), **patch).validate()


def test_real_mode_argv_is_unchanged_and_fake_conflicts_fail_before_generation(tmp_path):
    parser = argparse.ArgumentParser()
    add_market_arguments(parser)
    real = parser.parse_args([])
    remaining = ['--logs-dir', 'Daily_bot/logs']
    assert prepare_market_args(real, remaining) is remaining
    fake = parser.parse_args(['--market-source','fake','--fake-output-dir',str(tmp_path/'blocked')])
    for token in ['--db=live.sqlite3','--logs-dir','--use-selected-signals','--use-actual-fill-exits','--trend-filter-enabled']:
        with pytest.raises(ValueError, match='cannot be combined'):
            prepare_market_args(fake, [token])
    assert not (tmp_path/'blocked').exists()


def test_fake_mode_prepares_isolated_db_and_viewer(tmp_path):
    parser = argparse.ArgumentParser()
    add_market_arguments(parser)
    out = tmp_path/'run'
    args = parser.parse_args(['--market-source','fake','--fake-output-dir',str(out),
                              '--fake-stocks','5','--fake-ticks-per-day','10'])
    remaining = prepare_market_args(args, ['--starting-capital-krw','1000000'])
    assert remaining[0:2] == ['--db',str(out/'replay.sqlite3')]
    assert '--logs-dir' not in remaining
    assert '--ignore-selected-signals' in remaining
    assert str(out/'backtest_fake.csv') in remaining
    viewer = next(out.glob('*.html')).read_text()
    assert '__PAYLOAD__' not in viewer and 'FAKE01' in viewer
    assert 'https://' not in viewer  # self-contained


@pytest.mark.parametrize("fill_model", ["legacy", "conservative"])
def test_cli_fake_and_recorded_paths_replay_identical_trace_equally(tmp_path, fill_model):
    import os
    import shutil
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root))
    fake_dir = tmp_path/'fake'
    common = [sys.executable, '-m', 'Daily_bot.backtest.replay_refill_threshold',
              '--refill-min-expected-return', '0.90', '--starting-capital-krw', '1000000',
              '--fill-model', fill_model]
    fake = subprocess.run(common + ['--market-source','fake','--fake-profile','legacy','--fake-output-dir',str(fake_dir),
                                   '--fake-stocks','5','--fake-ticks-per-day','380','--fake-no-viewer'],
                          cwd=tmp_path, env=env, capture_output=True, text=True)
    assert fake.returncode == 0, fake.stdout+fake.stderr
    real_dir = tmp_path/'recorded'; real_dir.mkdir()
    shutil.copyfile(next(fake_dir.glob('fake_market_traces_*.csv')), real_dir/'market_traces_20261123.csv')
    recorded_out = tmp_path/'recorded.csv'
    real = subprocess.run(common + ['--market-source','real','--logs-dir',str(real_dir),'--out',str(recorded_out)],
                          cwd=tmp_path, env=env, capture_output=True, text=True)
    assert real.returncode == 0, real.stdout+real.stderr
    assert (fake_dir/'backtest_fake.csv').read_bytes() == recorded_out.read_bytes()
    assert (fake_dir/'backtest_fake_daily_rev.csv').read_bytes() == (tmp_path/'recorded_daily_rev.csv').read_bytes()
    if fill_model == 'legacy':
        assert len(list(csv.DictReader(recorded_out.open(encoding='utf-8-sig')))) > 0
    else:
        import json
        execution = json.loads((tmp_path/'recorded_execution.json').read_text(encoding='utf-8'))
        assert execution['config']['buy_timeout_seconds'] == 30
        assert execution['config']['buy_fill_poll_seconds'] == 2
        assert execution['config']['allow_signal_snapshot_buy'] is False
