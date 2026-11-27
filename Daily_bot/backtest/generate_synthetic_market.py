from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path

from Daily_bot.models import Candidate, HogaLevel, HogaSnapshot
from Daily_bot.storage.db import SCHEMA
from Daily_bot.strategy.signal import calc_expected_return
from Daily_bot.utils import load_yaml, move_price_by_ticks, round_to_tick


CONFIG = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
START_PRICES = (2100, 2750, 5100, 6800, 10100, 20600, 27900, 41200, 51600, 77000, 102000, 155000)


def sessions(start: date, end: date):
    while start < end:
        if start.weekday() < 5:
            yield start
        start += timedelta(days=1)


def nearest_tick(price: float) -> int:
    lower = round_to_tick(max(500, int(price)))
    upper = move_price_by_ticks(lower, 1)
    return lower if price - lower < upper - price else upper


def generate(out: Path, start: date, end: date, seed: int) -> tuple[int, int]:
    rng = random.Random(seed)
    cfg = load_yaml(CONFIG)
    strategy = cfg["strategy"]
    universe = cfg["universe"]
    tick_offset = strategy["sell_tick_offset"]
    prices = list(START_PRICES)
    shares = [1_000_000_000_000 // price for price in prices]
    dates = list(sessions(start, end))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch(exist_ok=False)
    row_count = 0
    with closing(sqlite3.connect(out)) as conn, conn:
        conn.executescript(SCHEMA)
        for day_number, day in enumerate(dates, start=1):
            previous_close = prices.copy()
            market_caps = [price * count for price, count in zip(previous_close, shares)]
            trading_values = [5_000_000_000 + index * 500_000_000 for index in range(len(prices))]
            phases = [
                "scan_candidate"
                if cap >= universe["min_market_cap_krw"] and value >= universe["min_trading_value_krw"]
                else "market_snapshot"
                for cap, value in zip(market_caps, trading_values)
            ]
            pressure = [rng.gauss(0, 0.7) for _ in prices]
            overnight = rng.gauss(0, 0.006)
            prices = [nearest_tick(price * (1 + overnight + rng.gauss(0, 0.005))) for price in prices]
            market_drift = rng.gauss(0, 0.00012)
            rows = []
            for minute in range(6 * 60 + 16):
                timestamp = (datetime.combine(day, time(9)) + timedelta(minutes=minute)).isoformat(sep=" ")
                market_move = rng.gauss(market_drift, 0.0005)
                for index, price in enumerate(prices):
                    ticker = f"SYN{index + 1:03d}"
                    pressure[index] = pressure[index] * 0.84 + rng.gauss(0, 0.72)
                    move = market_move + pressure[index] * 0.0006 + rng.gauss(0, 0.0016)
                    price = nearest_tick(price * (1 + move))
                    prices[index] = price
                    imbalance = math.exp(max(-2.5, min(2.5, pressure[index])))
                    bids = []
                    asks = []
                    bid_price = ask_price = price
                    for _ in range(10):
                        bid_price = move_price_by_ticks(bid_price, -1)
                        ask_price = move_price_by_ticks(ask_price, 1)
                        bids.append(HogaLevel(bid_price, max(1, round(rng.lognormvariate(7, 0.6) * imbalance))))
                        asks.append(HogaLevel(ask_price, max(1, round(rng.lognormvariate(7, 0.6) / imbalance))))
                    snapshot = HogaSnapshot(ticker=ticker, current_price=price, bids=bids, asks=asks)
                    candidate = calc_expected_return(
                        Candidate(ticker=ticker, prev_close_price=previous_close[index]), snapshot,
                        sell_tick_offset=tick_offset, strategy_cfg=strategy,
                    )
                    raw = json.dumps({
                        "market": "KOSPI" if index < len(prices) // 2 else "KOSDAQ",
                        "synthetic_bids": [(level.price, level.volume) for level in bids],
                        "synthetic_asks": [(level.price, level.volume) for level in asks],
                    }, separators=(",", ":"))
                    rows.append((
                        day.isoformat(), phases[index], ticker, timestamp, price, previous_close[index], price,
                        bids[0].price, asks[0].price, candidate.expect_price, candidate.expect_revenue_percent,
                        candidate.spread_percent, candidate.ask_depth_5_amount_krw,
                        market_caps[index], trading_values[index],
                        candidate.prev_day_change_percent, raw,
                    ))
            conn.executemany(
                """INSERT INTO market_traces (
                    session_date, phase, ticker, scan_cycle_at, price, prev_close_price, current_price,
                    best_bid, best_ask, expect_price, expect_revenue_percent, spread_percent,
                    ask_depth_5_amount_krw, market_cap, trading_value, prev_day_change_percent, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            row_count += len(rows)
            if day_number == len(dates) or day_number % 20 == 0:
                print(f"Generated through {day}: {row_count:,} rows", flush=True)
    return len(dates), row_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reproducible synthetic Daily Bot market traces.")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="Exclusive end date")
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not list(sessions(args.start, args.end)):
        parser.error("Date range must contain at least one weekday")
    count, rows = generate(args.out, args.start, args.end, args.seed)
    print(f"{args.out}: {count} synthetic sessions, {rows:,} rows, seed={args.seed}")


if __name__ == "__main__":
    main()
