"""Compare identical causal order-book signals on recorded and synthetic traces.

Only anchors spaced >=60s within the entry window are used. Future observations
must be at/after the requested horizon and within tolerance; never nearest-before.
CSV, csv.gz, and ZIPs containing those traces are supported, without extraction.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import io
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from Daily_bot.backtest import replay_market_traces as base


def trace_streams(paths):
    for path in paths:
        path = Path(path)
        if path.suffix == '.zip':
            with ZipFile(path) as archive:
                for name in sorted(archive.namelist()):
                    if 'market_traces_' not in name or not name.endswith(('.csv', '.csv.gz')):
                        continue
                    with archive.open(name) as stream:
                        if name.endswith('.gz'):
                            with gzip.GzipFile(fileobj=stream) as inflated:
                                yield f'{path.name}:{name}', io.TextIOWrapper(inflated, encoding='utf-8-sig')
                        else:
                            yield f'{path.name}:{name}', io.TextIOWrapper(stream, encoding='utf-8-sig')
        else:
            opener = gzip.open if path.suffix == '.gz' else open
            with opener(path, 'rt', encoding='utf-8-sig', newline='') as stream:
                yield str(path), stream


def timestamp(row, raw):
    value = row.get('created_at')
    if value:
        return datetime.fromisoformat(value)
    clock = str(raw.get('bid_req_base_tm') or '')
    if len(clock) == 6 and clock.isdigit():
        return datetime.fromisoformat(f"{row['session_date']} {clock[:2]}:{clock[2:4]}:{clock[4:]}")
    value = row.get('scan_cycle_at')
    return datetime.fromisoformat(value) if value else None


def collect(paths, weight=0.0):
    groups = defaultdict(dict)
    counts = defaultdict(int)
    sources = []
    for name, stream in trace_streams(paths):
        sources.append(name)
        for row in csv.DictReader(stream):
            counts['input_rows'] += 1
            try:
                raw = json.loads(row.get('raw_json') or '{}')
                now = timestamp(row, raw)
                bids = base._extract_hoga_levels_from_raw(raw, 'bid', 10)
                asks = base._extract_hoga_levels_from_raw(raw, 'ask', 10)
                price = abs(int(float(row.get('current_price') or row.get('price') or 0)))
                if now is None or not bids or not asks or price <= 0 or bids[0].price >= asks[0].price:
                    counts['invalid_rows'] += 1
                    continue
                is_scan = row.get('phase') == 'scan_candidate'
                signal = imbalance = None
                if is_scan:
                    snap = base.HogaSnapshot(row['ticker'], price, bids, asks)
                    weighted = base.apply_orderbook_decay(snap, weight, weight)
                    expected = base.predict_price_from_hoga(weighted)
                    signal = (base.calc_target_sell_price(expected, 1) / price - 1) * 100
                    bv = sum(v.volume for v in weighted.bids)
                    av = sum(v.volume for v in weighted.asks)
                    imbalance = (bv-av)/(bv+av) if bv+av else 0
                key = (row['session_date'], row['ticker'])
                if now in groups[key]:
                    counts['duplicate_timestamps'] += 1
                    if groups[key][now][4] and not is_scan:
                        continue
                groups[key][now] = (price, (bids[0].price+asks[0].price)/2, signal, imbalance, is_scan)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                counts['invalid_rows'] += 1
        print(f'Loaded {name}', flush=True)
    return groups, dict(counts), sources


def observations(groups, horizons=(10, 60, 300), tolerance=3, spacing=60):
    output = []
    for (day, ticker), data in sorted(groups.items()):
        times = sorted(data)
        epochs = [t.timestamp() for t in times]
        previous = float('-inf')
        for i, now in enumerate(times):
            if not data[now][4] or not '09:30' <= now.strftime('%H:%M') < '11:30' or epochs[i]-previous < spacing:
                continue
            previous = epochs[i]
            price, mid, signal, imbalance, _ = data[now]
            for horizon in horizons:
                j = bisect.bisect_left(epochs, epochs[i]+horizon)
                if j == len(times) or epochs[j] > epochs[i]+horizon+tolerance:
                    continue
                later = data[times[j]]
                output.append(dict(session_date=day, ticker=ticker, timestamp=now.isoformat(),
                    horizon_seconds=horizon, actual_seconds=epochs[j]-epochs[i],
                    signal_percent=signal, imbalance=imbalance,
                    return_percent=(later[0]/price-1)*100,
                    mid_return_percent=(later[1]/mid-1)*100))
    return output


def summarize(rows):
    import pandas as pd
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    df['signal_bin'] = pd.cut(df.signal_percent, [-float('inf'), 0, .3, .71, 1, float('inf')],
        labels=['<=0', '0..0.30', '0.30..0.71', '0.71..1.00', '>1.00'])
    summaries = []
    for (horizon, bucket), part in df.groupby(['horizon_seconds', 'signal_bin'], observed=True):
        daily = part.groupby(['session_date', 'ticker']).return_percent.mean()
        summaries.append(dict(horizon_seconds=int(horizon), signal_bin=str(bucket), n=len(part),
            symbol_days=len(daily), mean_signal_percent=float(part.signal_percent.mean()),
            mean_return_percent=float(part.return_percent.mean()),
            mean_mid_return_percent=float(part.mid_return_percent.mean()),
            up_percent=float((part.return_percent>0).mean()*100),
            down_percent=float((part.return_percent<0).mean()*100),
            flat_percent=float((part.return_percent==0).mean()*100),
            equal_symbol_day_mean_return_percent=float(daily.mean())))
    return summaries


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--real', nargs='+', required=True)
    ap.add_argument('--synthetic', nargs='+', required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--weight', type=float, default=0.0)
    ap.add_argument('--tolerance-seconds', type=float, default=3)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = {'weight_floor': args.weight, 'tolerance_seconds': args.tolerance_seconds,
        'anchor_phase': 'scan_candidate', 'anchor_spacing_seconds': 60, 'entry_window': '09:30 <= t < 11:30',
        'limitations': ['Recorded candidate/watch sampling is selected and sparse.',
          'Synthetic sample covers supplied archives only; not the full three months.',
          'Different dates, securities, tick percentages and regimes; descriptive, not causal.',
          'Overlapping horizons; do not treat observations as independent trials.',
          'Neither price response nor signal accuracy is an executable strategy return.']}
    for label, paths in [('real', args.real), ('synthetic', args.synthetic)]:
        groups, counts, sources = collect(paths, args.weight)
        rows = observations(groups, tolerance=args.tolerance_seconds)
        result[label] = dict(counts=counts, sources=sources, symbol_days=len(groups),
            sessions=len({d for d, _ in groups}), summary=summarize(rows))
        if rows:
            with (args.out/f'{label}_observations.csv').open('w', newline='', encoding='utf-8-sig') as fp:
                w = csv.DictWriter(fp, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        del groups
    (args.out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v['summary'] for k,v in result.items() if k in {'real','synthetic'}}, indent=2))


if __name__ == '__main__':
    main()
