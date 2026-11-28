"""Stream entry-signal coverage before blaming slots or tuning a strategy.

CSV/CSV.GZ/ZIP inputs are read through the same raw-book extractor and predictor
as replay. Snapshot eligibility is not a stateful order/fill simulation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median, pstdev

from Daily_bot.backtest import replay_market_traces as base
from Daily_bot.backtest.check_signal_realism import trace_streams
from Daily_bot.utils import parse_hhmm


def quantiles(values):
    ordered = sorted(values)
    if not ordered:
        return {}
    return {str(q): ordered[round((len(ordered) - 1) * q / 100)]
            for q in (0, 5, 50, 95, 99, 100)}


def fit_depth_calibration(samples, sources):
    """Fit only depth dispersion; remove observed directional selection bias.

    One snapshot/symbol/day; ticker-disjoint 20% holdout. No future prices,
    entry-rate target, trade PnL, or optimized strategy threshold is an input.
    """
    unique = {}
    for sample in samples:
        unique.setdefault((sample['session_date'], sample['ticker']), sample)
    train, holdout = [], []
    train_prices = []
    for sample in unique.values():
        bucket = int.from_bytes(hashlib.sha256(sample['ticker'].encode()).digest()[:8], 'big') % 5
        (holdout if bucket == 0 else train).append(sample['log_depth_ratio'])
        if bucket != 0 and sample.get('price', 0) > 0:
            train_prices.append(sample['price'])
    if len(train) < 20 or len(holdout) < 5:
        raise ValueError('Depth calibration needs >=20 training and >=5 ticker-disjoint holdout samples')
    bounds = quantiles(train)
    low, high = bounds['5'], bounds['95']
    clipped = [min(high, max(low, value)) for value in train]
    sigma = pstdev(clipped)
    if not 0 < sigma <= 3:
        raise ValueError('Observed depth dispersion is outside the supported (0, 3] range')
    return {
        'schema_version': 1, 'sources': list(sources),
        'training_samples': len(train), 'holdout_samples': len(holdout),
        'depth_imbalance_sigma': sigma, 'depth_imbalance_persistence': .995,
        'initial_price_samples': train_prices,
        'removed_training_median_log_ratio': median(train),
        'training_log_ratio_quantiles': quantiles(train),
        'holdout_log_ratio_quantiles': quantiles(holdout),
        'fit': '5/95-percentile winsorized population SD of weighted log(bid_depth/ask_depth)',
        'holdout': 'SHA256(ticker) modulo 5 == 0; never used to fit sigma',
        'limitations': [
            'Source candidate snapshots are not an unconditional market population',
            'Initial synthetic price levels are bootstrapped from training snapshots only',
            'Directional sample bias is not injected: synthetic depth shocks are centered at zero',
            'Persistence .995 per simulated second is an assumption, not fitted from snapshots',
            'Stationary book dynamics can attenuate the fitted order-arrival dispersion',
            'Matching signal frequency does not validate conditional returns or executable profits',
        ],
    }


def diagnose(paths, *, threshold=.71, slot_budget=333333, start='09:30', stop='11:30',
             weight=0., sell_tick_offset=1, observed_trades=None):
    if not math.isfinite(threshold) or threshold < 0 or not math.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError('threshold must be finite/nonnegative and weight in [0,1]')
    if len(start) != 5 or len(stop) != 5 or parse_hhmm(start) > parse_hhmm(stop):
        raise ValueError('buy window must use ordered HH:MM clocks within one day')
    counts = Counter()
    per_day = defaultdict(Counter)
    per_ticker = defaultdict(Counter)
    metrics = defaultdict(list)
    seen = set()
    samples = []
    sources = []
    for name, stream in trace_streams(paths):
        sources.append(name)
        for row in csv.DictReader(stream):
            counts['input_rows'] += 1
            day = row.get('session_date', '')
            if day:
                per_day[day]['input_rows'] += 1
            stamp = row.get('scan_cycle_at') or row.get('created_at') or ''
            if row.get('phase') != 'scan_candidate':
                counts['not_scan_candidate'] += 1
                continue
            if not start <= stamp[11:16] <= stop:
                counts['outside_buy_window'] += 1
                continue
            counts['entry_scan_rows'] += 1
            per_day[day]['entry_scan_rows'] += 1
            ticker = row.get('ticker', '')
            key = (day, ticker, stamp)
            counts['duplicate_entry_keys'] += key in seen
            seen.add(key)
            try:
                raw = json.loads(row.get('raw_json') or '{}')
                if not isinstance(raw, dict):
                    raise ValueError('raw_json must be an object')
                price = abs(int(float(row.get('current_price') or row.get('price') or 0)))
                bids = base._extract_hoga_levels_from_raw(raw, 'bid', 10)
                asks = base._extract_hoga_levels_from_raw(raw, 'ask', 10)
                if price <= 0 or not bids or not asks:
                    counts['missing_price_or_book'] += 1
                    per_day[day]['unscorable_entry_rows'] += 1
                    continue
                crossed = bids[0].price >= asks[0].price
                counts['crossed_or_locked_quotes'] += crossed
                counts['partial_books'] += len(bids) < 10 or len(asks) < 10
                snap = base.HogaSnapshot(ticker, price, bids, asks)
                weighted = base.apply_orderbook_decay(snap, weight, weight)
                expected = base.predict_price_from_hoga(weighted)
                target = int(base.calc_target_sell_price(expected, sell_tick_offset))
                signal = (target / price - 1) * 100
                # All possible frontier pairs give a conservative geometric
                # upper bound, including price-band boundaries and one-tick exit.
                upper_target = max(int(base.calc_target_sell_price(
                    base.round_to_tick((bid.price + ask.price) // 2), sell_tick_offset))
                    for bid in bids for ask in asks)
                upper_signal = (upper_target / price - 1) * 100
                bv = sum(level.volume for level in weighted.bids)
                av = sum(level.volume for level in weighted.asks)
                imbalance = (bv - av) / (bv + av) if bv + av else 0.
                spread = (asks[0].price - bids[0].price) / price * 100
                tick = base.get_tick_size(price)
                metrics['signal_percent'].append(signal)
                metrics['geometric_ceiling_percent'].append(upper_signal)
                metrics['weighted_imbalance'].append(imbalance)
                metrics['tick_percent'].append(tick / price * 100)
                metrics['spread_percent'].append(spread)
                counts['scored_entry_rows'] += 1
                per_day[day]['scored_entry_rows'] += 1
                counts['geometrically_below_threshold'] += upper_signal < threshold
                counts['below_signal_threshold'] += signal < threshold
                passed = signal >= threshold and target > price
                affordable = slot_budget <= 0 or price <= slot_budget
                counts['signal_pass_rows'] += passed
                counts['signal_pass_but_one_share_unaffordable'] += passed and not affordable
                counts['signal_pass_and_affordable_rows'] += passed and affordable
                per_day[day]['signal_pass_rows'] += passed
                per_day[day]['affordable_signal_rows'] += passed and affordable
                per_ticker[ticker]['scored'] += 1
                per_ticker[ticker]['signal_pass'] += passed
                if 'expect_revenue_percent' in row:
                    counts['recomputed_differs_from_stored_signal'] += abs(signal - float(row['expect_revenue_percent'])) > 1e-6
                synthetic = bool(raw.get('synthetic'))
                counts['synthetic_entry_rows'] += synthetic
                if not crossed and bv > 0 and av > 0 and len(bids) == len(asks) == 10:
                    samples.append(dict(session_date=day, ticker=ticker, price=price,
                                        log_depth_ratio=math.log(bv / av), synthetic=synthetic))
            except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
                counts['unscorable_entry_rows'] += 1
                per_day[day]['unscorable_entry_rows'] += 1
    if not sources or not counts['input_rows']:
        raise ValueError('No market trace rows were found in the supplied inputs')
    trade_counts = Counter()
    if observed_trades is not None:
        for trade in observed_trades:
            trade_counts[trade['session_date']] += 1
    for day, item in per_day.items():
        if observed_trades is not None:
            item['observed_trades'] = trade_counts[day]
        if not item['entry_scan_rows']:
            reason = 'no_entry_scan_rows'
        elif not item['scored_entry_rows']:
            reason = 'invalid_or_missing_book'
        elif not item['signal_pass_rows']:
            reason = 'no_qualifying_signal'
        elif not item['affordable_signal_rows']:
            reason = 'no_affordable_signal'
        elif observed_trades is not None and not trade_counts[day]:
            reason = 'requires_stateful_replay_investigation'
        else:
            reason = 'signal_available'
        item['classification'] = reason
    result = dict(
        counts=dict(counts), sources=sources, per_session=dict(per_day), per_ticker=dict(per_ticker),
        quantiles={name: quantiles(values) for name, values in metrics.items()},
        config=dict(threshold_percent=threshold, slot_budget_krw=slot_budget, start=start, stop=stop,
                    window_end_inclusive=True, weight_floor=weight, sell_tick_offset=sell_tick_offset),
        tick_grid='unified KRX ordinary shares since 2023-01-25',
        limitations=[
            'Counts are snapshot rows; duplicate entry keys are reported, not silently removed',
            'Stateful position/slot/stop/reentry/cash/order constraints require complete strategy replay',
            'A geometric ceiling is an upper bound, not an attainable forecast',
            'Stored signal mismatches may reflect an older predictor version; current raw-book recomputation is authoritative',
        ],
    )
    return result, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', nargs='+', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--threshold', type=float, default=.71)
    ap.add_argument('--slot-budget-krw', type=int, default=333333)
    ap.add_argument('--start', default='09:30')
    ap.add_argument('--stop', default='11:30')
    ap.add_argument('--weight', type=float, default=0.)
    ap.add_argument('--sell-tick-offset', type=int, default=1)
    ap.add_argument('--trades', type=Path)
    ap.add_argument('--calibration-out', type=Path)
    args = ap.parse_args()
    trades = None
    if args.trades:
        with args.trades.open(encoding='utf-8-sig', newline='') as fp:
            trades = list(csv.DictReader(fp))
    result, samples = diagnose(args.input, threshold=args.threshold,
        slot_budget=args.slot_budget_krw, start=args.start, stop=args.stop,
        weight=args.weight, sell_tick_offset=args.sell_tick_offset, observed_trades=trades)
    if args.calibration_out:
        if any(sample['synthetic'] for sample in samples):
            ap.error('Depth calibration requires real snapshots; synthetic inputs are not allowed')
        calibration = fit_depth_calibration(samples, result['sources'])
        args.calibration_out.parent.mkdir(parents=True, exist_ok=True)
        args.calibration_out.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding='utf-8')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['counts'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
