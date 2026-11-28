"""Fit reference noise to nonoverlapping recorded price returns, without PnL.

Use fresh quote clocks where available; repeated snapshots at the same quote
clock collapse. Training dates and holdout dates must be supplied separately.
"""
from __future__ import annotations
import argparse
import bisect
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
import numpy as np
from Daily_bot.backtest.check_signal_realism import timestamp


def measure(paths, horizons=(60, 300, 900)):
    groups = defaultdict(dict)
    input_rows = invalid_rows = duplicates = 0
    sources = []
    for path in map(Path, paths):
        sources.append(dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                input_rows += 1
                try:
                    raw = json.loads(row.get('raw_json') or '{}')
                    now = timestamp(row, raw)
                    price = abs(float(row.get('current_price') or row.get('price') or 0))
                    if now is None or price <= 0 or not math.isfinite(price):
                        raise ValueError('missing price or clock')
                    key = (row['session_date'], row['ticker'])
                    duplicates += now in groups[key]
                    groups[key][now] = price
                except (ValueError, TypeError, KeyError):
                    invalid_rows += 1
    summaries = []
    for horizon in horizons:
        values, keys = [], set()
        for key, data in sorted(groups.items()):
            times = sorted(data)
            seconds = [(t-times[0]).total_seconds() for t in times]
            previous = -math.inf
            for i, now in enumerate(times):
                if seconds[i]-previous < horizon:
                    continue
                j = bisect.bisect_left(seconds, seconds[i]+horizon)
                if j == len(times) or seconds[j] > seconds[i]+horizon*1.2:
                    continue
                # Normalize the small timing tolerance to the intended horizon.
                values.append(math.log(data[times[j]]/data[now])*10000
                              * math.sqrt(horizon/(seconds[j]-seconds[i])))
                keys.add(key)
                previous = seconds[i]
        if not values:
            continue
        lo, hi = np.percentile(values, [1, 99])
        clipped = np.clip(values, lo, hi)
        sd = float(np.std(clipped))
        summaries.append(dict(horizon_seconds=horizon, observations=len(values),
            symbol_days=len(keys), winsorized_sd_bps=sd,
            flat_percent=100*sum(v == 0 for v in values)/len(values),
            reference_n=sd/math.sqrt(horizon*.37)))
    return dict(sources=sources, input_rows=input_rows, invalid_rows=invalid_rows,
                duplicate_quote_clocks=duplicates, horizons=summaries)


def fit(training, holdout):
    train = measure(training)
    if not train['horizons'] or sum(v['observations'] for v in train['horizons']) < 100:
        raise ValueError('insufficient matched training observations')
    # Equal horizon weights, rather than letting dense monitoring of one ticker dominate.
    n = float(np.median([v['reference_n'] for v in train['horizons']]))
    return dict(schema_version=1, reference_n=n, previous_reference_n=1.6,
        noise_multiplier=n/1.6, method='median of 1/5/15-minute 1%-winsorized return SD / sqrt(horizon * .37)',
        training=train, holdout=measure(holdout),
        limitations=['selected bot-observed stocks, not a representative Korean market sample',
            'quote clocks deduplicated; no exchange trade tape or full freshness audit',
            'depth persistence, order arrivals, market correlation and bear multiplier are scenario assumptions',
            'reference calibration does not guarantee the same realized last-trade volatility'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training', nargs='+', required=True)
    parser.add_argument('--holdout', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = fit(args.training, args.holdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
