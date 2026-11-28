"""Rebuild fixed 0001-01..06 weak-bull/strong-bear synthetic stress data.

The schedule and seed are fixed before measuring trading results. Quarter-end
market references are conditioned scenario endpoints, not return forecasts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import numpy as np
from Daily_bot.backtest.fake_market import floor_price, generate_market, korean_config


def reference_scenario(cfg):
    cfg.validate()
    if cfg.stocks != 10 or cfg.start_date != '0001-01-01' or cfg.days != 130 or not cfg.include_session_end:
        raise ValueError('mixed scenario requires 10 stocks, 130 sessions from 0001-01-01, including session end')
    rng = np.random.default_rng(cfg.seed)
    universe_rng = random.Random(f'initial-prices:{cfg.seed}')
    previous = np.array([floor_price(universe_rng.choice(cfg.initial_price_samples))
                         for _ in range(cfg.stocks)], dtype=float)
    tickers = [f'FAKE{i+1:02d}' for i in range(cfg.stocks)]
    days = []
    day = date.fromisoformat(cfg.start_date)
    while day <= date(1, 6, 30):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    paths, regimes = {}, []
    steps = cfg.ticks_per_day-1
    dt = cfg.session_seconds/steps
    for name, dates, daily_drift, multiplier in [
        ('weak_bull', [v for v in days if v.month <= 3], .0003, 1.0),
        ('strong_bear', [v for v in days if v.month > 3], -.006, 1.5),
    ]:
        count = len(dates)*steps
        fractions = np.arange(count+1)/count
        sigma = cfg.n*np.sqrt(.37*dt)/10000*multiplier
        # Independent and common Gaussian paths have the calibrated second moment.
        common = np.r_[0., np.cumsum(rng.normal(0, sigma, count))]
        common -= fractions*common[-1]
        market = np.exp(fractions*len(dates)*np.log1p(daily_drift)+.35*common)
        idio = np.vstack([np.zeros(cfg.stocks),
                          np.cumsum(rng.normal(0, sigma*np.sqrt(1-.35**2), (count,cfg.stocks)), axis=0)])
        idio -= fractions[:,None]*idio[-1]
        relative = np.exp(idio)
        relative *= (market/relative.mean(axis=1))[:,None]
        absolute = relative*previous
        for i, day in enumerate(dates):
            paths[str(day)] = {ticker:absolute[i*steps:(i+1)*steps+1,j]
                               for j,ticker in enumerate(tickers)}
        previous = absolute[-1].copy()
        regimes.append(dict(name=name, sessions=len(dates), daily_drift=daily_drift,
            volatility_multiplier=multiplier, reference_quarter_return_percent=(market[-1]-1)*100))
    return paths, dict(regimes=regimes, common_loading=.35,
        reference_model='Gaussian diffusion; quarter Brownian bridges; equal-weight market normalization',
        endpoints_conditioned=True, intraday_seconds=dt,
        opening_gaps='continuous reference; small execution/reference gaps can remain overnight',
        settings=asdict(cfg))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=20261128)
    ap.add_argument('--auction-vi',action='store_true')
    args = ap.parse_args()
    cfg = korean_config(seed=args.seed, start_date='0001-01-01', days=130, stocks=10, auction_vi=args.auction_vi)
    paths, scenario = reference_scenario(cfg)
    generate_market(args.output, cfg, viewer=False, reference_paths=paths, archive_months=True)
    scenario['archives'] = [dict(path=p.name, bytes=p.stat().st_size,
        sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(args.output.glob('*.zip'))]
    (args.output/'scenario.json').write_text(json.dumps(scenario,indent=2)+'\n')
    # Include the complete per-session checksums/config without duplicating raw CSV.
    with ZipFile(args.output/'dailybot_korean_mixed_0001_metadata.zip','w',ZIP_DEFLATED) as archive:
        for name in ('scenario.json','manifest.json'):
            archive.write(args.output/name,name)
    print(json.dumps(scenario['regimes'],indent=2),flush=True)


if __name__ == '__main__':
    main()
