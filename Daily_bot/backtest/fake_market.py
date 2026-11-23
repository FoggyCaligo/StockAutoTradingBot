"""Seeded synthetic continuous-auction market, isolated from live trading.

The latent reference drives order flow; only executions change last_price.
No broker, external data provider, or API credentials are used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from Daily_bot.models import HogaLevel, HogaSnapshot
from Daily_bot.strategy.orderbook_predictor import (
    apply_orderbook_decay, calc_ask_depth_amount, calc_spread_percent,
    calc_target_sell_price, predict_price_from_hoga,
)


MODEL_VERSION = 1
FIELDS = [
    "session_date", "phase", "ticker", "scan_cycle_at", "selected", "reason",
    "price", "prev_close_price", "current_price", "best_bid", "best_ask",
    "expect_price", "expect_revenue_percent", "spread_percent",
    "ask_depth_5_amount_krw", "prev_day_change_percent", "raw_json", "created_at",
]


def tick_size(price: int) -> int:
    """KRX ordinary-share grid (2023 unified KOSPI/KOSDAQ schedule)."""
    for bound, step in [(2000, 1), (5000, 5), (20000, 10), (50000, 50),
                        (200000, 100), (500000, 500)]:
        if price < bound:
            return step
    return 1000


def floor_price(price: float) -> int:
    value = max(1, int(price))
    return max(1, value // tick_size(value) * tick_size(value))


def adjacent(price: int, direction: int) -> int:
    return max(1, price + (tick_size(price) if direction > 0 else -tick_size(price - 1)))


@dataclass
class Order:
    sequence: int
    quantity: int


@dataclass(frozen=True)
class Execution:
    price: int
    quantity: int
    resting_sequence: int
    aggressor_side: str


class OrderBook:
    """Price priority, FIFO within price, partial fills and quantity cancellation."""

    def __init__(self, previous_close: int):
        self.last_price = previous_close  # assumed opening reference, until first trade
        self.lower = floor_price(previous_close * .70)
        if self.lower < previous_close * .70:
            self.lower = adjacent(self.lower, 1)
        self.upper = floor_price(previous_close * 1.30)
        self.bids: dict[int, deque[Order]] = {}
        self.asks: dict[int, deque[Order]] = {}
        self.sequence = 0

    def submit(self, side: str, quantity: int, price: int | None = None) -> list[Execution]:
        if side not in {"buy", "sell"} or quantity <= 0:
            raise ValueError("side must be buy/sell and quantity must be positive")
        if price is not None and (not self.lower <= price <= self.upper or floor_price(price) != price):
            raise ValueError("limit price must be on grid and within daily limits")
        self.sequence += 1
        opposite = self.asks if side == "buy" else self.bids
        own = self.bids if side == "buy" else self.asks
        fills = []
        while quantity and opposite:
            best = min(opposite) if side == "buy" else max(opposite)
            if price is not None and ((side == "buy" and price < best) or (side == "sell" and price > best)):
                break
            queue = opposite[best]
            order = queue[0]
            filled = min(quantity, order.quantity)
            fills.append(Execution(best, filled, order.sequence, side))
            self.last_price = best
            quantity -= filled
            order.quantity -= filled
            if not order.quantity:
                queue.popleft()
            if not queue:
                del opposite[best]
        if quantity and price is not None:
            own.setdefault(price, deque()).append(Order(self.sequence, quantity))
        # A market order's unfilled residual is cancelled, never stored at a fake price.
        return fills

    def cancel(self, side: str, price: int, quantity: int) -> int:
        """Cancel oldest remaining synthetic orders at a level; never executes."""
        levels = self.bids if side == "buy" else self.asks
        queue = levels.get(price)
        cancelled = 0
        while queue and quantity > 0:
            amount = min(queue[0].quantity, quantity)
            queue[0].quantity -= amount
            quantity -= amount
            cancelled += amount
            if not queue[0].quantity:
                queue.popleft()
        if queue is not None and not queue:
            del levels[price]
        return cancelled

    def snapshot(self, side: str, depth: int = 10) -> list[list[int]]:
        levels = self.bids if side == "buy" else self.asks
        return [[p, sum(o.quantity for o in levels[p])]
                for p in sorted(levels, reverse=side == "buy")[:depth]]


@dataclass(frozen=True)
class MarketConfig:
    seed: int = 42
    days: int = 1
    start_date: str = "2026-11-23"
    stocks: int = 0  # 0 = seeded independent 5..10 count per synthetic session
    ticks_per_day: int = 22800  # 09:00 <= time < 15:20 (continuous session only)
    x: float = 100.0  # reference slope: basis points per session, may be negative
    n: float = 1.0  # reference change scale, basis points
    a: float = 5.0  # hourly slope magnitude a*n..b*n basis points/hour
    b: float = 20.0
    c: float = 0.1  # independent signed tick change c*n..d*n basis points/tick
    d: float = 1.0
    base_quantity: int = 100
    cancellation_probability: float = .45
    market_order_scale: float = 4.0
    scan_interval_seconds: float = 60.0
    market_order_probability: float = 1.0
    max_market_order_levels: int = 0  # 0 = legacy uncapped aggressive orders
    profile: str = "legacy"

    def validate(self) -> None:
        if self.profile not in {"legacy", "ordinary-bull"}:
            raise ValueError("profile must be legacy or ordinary-bull")
        date.fromisoformat(self.start_date)
        if self.days < 1 or self.ticks_per_day < 2 or self.ticks_per_day > 22800000:
            raise ValueError("days >= 1; 2 <= ticks_per_day <= 22800000")
        if self.stocks != 0 and not 5 <= self.stocks <= 10:
            raise ValueError("stocks must be 0 (random 5..10), or 5..10")
        if any(not math.isfinite(v) for v in (self.x, self.n, self.a, self.b, self.c, self.d,
                                              self.scan_interval_seconds, self.cancellation_probability,
                                              self.market_order_scale, self.market_order_probability)):
            raise ValueError("parameters must be finite")
        if self.x <= -10000 or self.n < 0 or not 0 <= self.a <= self.b or not 0 <= self.c <= self.d:
            raise ValueError("x > -10000 bps; n >= 0; 0 <= a <= b; 0 <= c <= d")
        if self.base_quantity <= 0 or not 0 <= self.cancellation_probability <= 1:
            raise ValueError("base_quantity > 0; cancellation_probability in [0,1]")
        if self.market_order_scale < 0 or self.scan_interval_seconds <= 0:
            raise ValueError("market_order_scale >= 0; scan_interval_seconds > 0")
        if not 0 <= self.market_order_probability <= 1:
            raise ValueError("market_order_probability must be in [0,1]")
        if not isinstance(self.max_market_order_levels, int) or self.max_market_order_levels < 0:
            raise ValueError("max_market_order_levels must be a nonnegative integer")


def ordinary_bull_config(**overrides) -> MarketConfig:
    """Approximate moderate uptrend; measured quote targets, not a fitted tick tape.

    +7bp/session is approximately +19.3% over 252 sessions. n=1.6 gives
    approximately 1.47% reference tick-noise SD over 22,800 one-second steps.
    These are scenario design targets, not forecasts or guaranteed returns.
    """
    cfg = MarketConfig(profile="ordinary-bull", x=7.0, n=1.6, a=1.0, b=3.0, base_quantity=15,
                       cancellation_probability=.20, market_order_scale=1.0,
                       market_order_probability=.50, max_market_order_levels=1)
    return replace(cfg, **overrides)


def signed_uniform(rng: random.Random, lo: float, hi: float) -> float:
    return rng.choice((-1, 1)) * rng.uniform(lo, hi)


def _quote_prices(book: OrderBook, depth: int = 10, reference: float | None = None) -> tuple[list[int], list[int]]:
    # Persistent levels are never moved. New liquidity arrives around last trade.
    bid = min(book.last_price, min(book.asks) - tick_size(min(book.asks) - 1)) if book.asks else book.last_price
    ask = max(adjacent(book.last_price, 1), adjacent(max(book.bids), 1)) if book.bids else adjacent(book.last_price, 1)
    if reference is not None:
        # Replenishing bids at the last sell price but asks one tick above the
        # last buy price creates a mechanical upward ratchet. Normal-profile
        # participants quote around their noisy valuation instead. A newly
        # marketable limit still matches resting orders at their prices.
        bid = floor_price(reference)
        ask = adjacent(bid, 1)
    bids, asks = [], []
    for _ in range(depth):
        if book.lower <= bid <= book.upper:
            bids.append(bid)
        if book.lower <= ask <= book.upper:
            asks.append(ask)
        bid, ask = adjacent(bid, -1), adjacent(ask, 1)
    return bids, asks


def advance_book(book: OrderBook, reference: float, rng: random.Random, cfg: MarketConfig) -> list[Execution]:
    gap_ticks = (reference - book.last_price) / tick_size(book.last_price)
    pressure = math.tanh(gap_ticks / 3)
    # Every visible level on both sides receives an independent add/cancel draw.
    for side in ("buy", "sell"):
        for price, volume in book.snapshot(side):
            if rng.random() < cfg.cancellation_probability:
                book.cancel(side, price, int(volume * rng.uniform(.1, .7)))
        # Far-away orders expire; the displayed top ten are not the entire book.
        levels = book.bids if side == "buy" else book.asks
        for price in list(levels):
            if abs(price - book.last_price) > 60 * tick_size(book.last_price):
                book.cancel(side, price, sum(o.quantity for o in levels[price]))
    bids, asks = _quote_prices(book, reference=reference if cfg.profile == "ordinary-bull" else None)
    submissions = [("buy", p) for p in bids] + [("sell", p) for p in asks]
    rng.shuffle(submissions)  # no deterministic buy-first ordering
    fills = []
    for side, price in submissions:
        bias = pressure if side == "buy" else -pressure
        quantity = max(1, int(cfg.base_quantity * rng.uniform(.2, 1.8) * math.exp(bias)))
        fills.extend(book.submit(side, quantity, price))
    # Independently sampled aggressive orders consume actual resting liquidity.
    aggressors = ["buy", "sell"]
    rng.shuffle(aggressors)
    for side in aggressors:
        if cfg.market_order_probability < 1 and rng.random() >= cfg.market_order_probability:
            continue
        bias = pressure if side == "buy" else -pressure
        quantity = int(cfg.base_quantity * cfg.market_order_scale * rng.random() * math.exp(2 * bias))
        if cfg.max_market_order_levels:
            opposite = book.snapshot("sell" if side == "buy" else "buy", cfg.max_market_order_levels)
            quantity = min(quantity, sum(q for _, q in opposite))
        if quantity:
            fills.extend(book.submit(side, quantity))
    return fills


def _raw(book: OrderBook, ticker: str, reference: float, fills: list[Execution]) -> dict:
    raw = {"synthetic": True, "model_version": MODEL_VERSION, "cur_prc": book.last_price,
           "reference_price": round(reference, 6), "tick_volume": sum(f.quantity for f in fills),
           "tick_turnover": sum(f.quantity * f.price for f in fills),
           "executions": [asdict(f) for f in fills]}
    for side, prefix in (("buy", "buy"), ("sell", "sel")):
        for i, (p, q) in enumerate(book.snapshot(side), 1):
            key = f"{prefix}_fpr" if i == 1 else f"{prefix}_{i}th_pre"
            raw[key + "_bid"] = p
            raw[key + "_req"] = q
    return raw


def generate_market(out_dir: Path, cfg: MarketConfig, *, viewer: bool = True) -> Path:
    """Create isolated fake_market_traces CSVs and a reproducibility manifest."""
    cfg.validate()
    # A run owns its directory. Refuse reuse so stale sessions can never mix in.
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise FileExistsError(f"Use an empty directory for a synthetic run: {out_dir}")
    rng = random.Random(cfg.seed)
    selection_rng = random.Random(cfg.seed) if cfg.profile == "ordinary-bull" else rng
    previous = {f"FAKE{i:02d}": floor_price(p) for i, p in enumerate(
        [1500, 3500, 7500, 15000, 25000, 45000, 75000, 150000, 250000, 550000], 1)}
    current_day = date.fromisoformat(cfg.start_date)
    manifest = {"model_version": 2 if cfg.profile == "ordinary-bull" else MODEL_VERSION,
                "synthetic": True, "config": asdict(cfg),
                "session": "09:00:00 <= Asia/Seoul time < 15:20:00; weekdays, holidays ignored",
                "limitations": ["assumed opening reference; no opening/closing call auction, VI or halts",
                                "random model is not calibrated to real market distributions",
                                "strategy replay remains snapshot-based with no bot market impact"],
                "files": []}
    for day_index in range(cfg.days):
        while current_day.weekday() >= 5:
            current_day += timedelta(days=1)
        count = cfg.stocks or selection_rng.randint(5, 10)
        tickers = selection_rng.sample(list(previous), count)
        simulated_tickers = list(previous) if cfg.profile == "ordinary-bull" else tickers
        recorded_tickers = set(tickers)
        # Exogenous reference draws must not change when execution-flow settings change.
        noise_rngs = {ticker: random.Random(int.from_bytes(hashlib.sha256(
            f"ordinary-bull-reference:{cfg.seed}:{current_day}:{ticker}".encode()).digest()[:8], "big"))
            for ticker in simulated_tickers} if cfg.profile == "ordinary-bull" else {}
        books = {ticker: OrderBook(previous[ticker]) for ticker in simulated_tickers}
        hourly = {}
        noise = dict.fromkeys(simulated_tickers, 0.0)
        series = {ticker: [] for ticker in tickers} if viewer else None
        path = out_dir / f"fake_market_traces_{current_day:%Y%m%d}.csv"
        start = datetime.combine(current_day, time(9))
        dt = 22800 / cfg.ticks_per_day
        scan_bucket = -1
        with path.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=FIELDS)
            writer.writeheader()
            for index in range(cfg.ticks_per_day):
                elapsed = index * dt
                now = start + timedelta(seconds=elapsed)
                bucket = int(elapsed // cfg.scan_interval_seconds)
                phase = "scan_candidate" if bucket != scan_bucket else "watch"
                scan_bucket = bucket
                stamp = now.isoformat(sep=" ", timespec="microseconds")
                for ticker in simulated_tickers:
                    book = books[ticker]
                    ref_rng = noise_rngs.get(ticker, rng)
                    hour = int(elapsed // 3600)
                    if (ticker, hour) not in hourly:
                        hourly[ticker, hour] = signed_uniform(ref_rng, cfg.a, cfg.b) * cfg.n
                    if index:
                        noise[ticker] += hourly[ticker, hour] * dt / 3600
                        scale = math.sqrt(dt) if cfg.profile == "ordinary-bull" else 1.0
                        noise[ticker] += signed_uniform(ref_rng, cfg.c, cfg.d) * cfg.n * scale
                    reference = previous[ticker] * (1 + (cfg.x * elapsed / 22800 + noise[ticker]) / 10000)
                    reference = max(book.lower, min(book.upper, reference))
                    fills = advance_book(book, reference, rng, cfg)
                    if ticker not in recorded_tickers:
                        continue
                    raw = _raw(book, ticker, reference, fills)
                    if cfg.profile == "ordinary-bull":
                        raw.update(model_version=2, market_profile=cfg.profile)
                    bids, asks = book.snapshot("buy"), book.snapshot("sell")
                    snapshot = HogaSnapshot(ticker, book.last_price,
                                            [HogaLevel(*v) for v in bids], [HogaLevel(*v) for v in asks])
                    expected = predict_price_from_hoga(apply_orderbook_decay(snapshot, 0.0, 0.0))
                    target = int(calc_target_sell_price(expected, 1))
                    writer.writerow(dict(
                        session_date=str(current_day), phase=phase, ticker=ticker,
                        scan_cycle_at=stamp if phase == "scan_candidate" else "", selected=0,
                        reason="synthetic_continuous_auction", price=book.last_price,
                        prev_close_price=previous[ticker], current_price=book.last_price,
                        best_bid=bids[0][0] if bids else 0, best_ask=asks[0][0] if asks else 0,
                        expect_price=expected, expect_revenue_percent=(target / book.last_price - 1) * 100,
                        spread_percent=calc_spread_percent(snapshot),
                        ask_depth_5_amount_krw=calc_ask_depth_amount(snapshot),
                        prev_day_change_percent=(book.last_price / previous[ticker] - 1) * 100,
                        raw_json=json.dumps(raw, separators=(",", ":")), created_at=stamp,
                    ))
                    if series is not None:
                        series[ticker].append([round(elapsed, 6), book.last_price, round(reference, 4),
                                               bids, asks, raw["tick_volume"]])
        previous_closes = {ticker: previous[ticker] for ticker in simulated_tickers}
        for ticker in simulated_tickers:
            previous[ticker] = books[ticker].last_price
        digest = hashlib.sha256()
        with path.open("rb") as fp:
            for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest["files"].append({"path": path.name, "sha256": digest.hexdigest(),
                                  "rows": count * cfg.ticks_per_day, "tickers": tickers})
        if cfg.profile == "ordinary-bull":
            manifest["files"][-1].update(previous_close_by_ticker=previous_closes,
                                         closing_price_by_ticker=dict(previous))
        if series is not None:
            from Daily_bot.backtest.fake_market_viewer import write_viewer
            write_viewer(out_dir / f"market_{current_day:%Y%m%d}.html", series, asdict(cfg), str(current_day))
        current_day += timedelta(days=1)
        print(f"Synthetic session {day_index + 1}/{cfg.days}: {path} ({count * cfg.ticks_per_day} rows)")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir


def add_market_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--market-source", choices=("real", "fake"), default="real",
                        help="real: existing recorded traces; fake: generate an isolated synthetic run")
    parser.add_argument("--fake-output-dir", default="", help="empty destination directory; default is a unique run")
    parser.add_argument("--fake-profile", choices=("ordinary-bull", "legacy"), default="ordinary-bull",
                        help="ordinary-bull: moderate uptrend and bounded aggressive flow; legacy: original model")
    for name, default, kind, help_text in [
        ("seed", 42, int, "reproducible RNG seed"), ("days", 1, int, "synthetic weekday sessions"),
        ("start-date", "2026-11-23", str, "first date (KST)"),
        ("stocks", 0, int, "0=random 5..10 per day, or fixed 5..10"),
        ("ticks-per-day", 22800, int, "t snapshots/stock/day; default one simulated second"),
        ("x", 100., float, "reference slope in bps/session (negative=down)"),
        ("n", 1., float, "reference change scale in bps"),
        ("a", 5., float, "hourly magnitude lower multiplier"),
        ("b", 20., float, "hourly magnitude upper multiplier"),
        ("c", .1, float, "tick magnitude lower multiplier"),
        ("d", 1., float, "tick magnitude upper multiplier"),
        ("base-quantity", 100, int, "base shares per passive order"),
        ("cancellation-probability", .45, float, "independent cancellation probability per level"),
        ("market-order-scale", 4., float, "aggressive order size multiplier"),
        ("market-order-probability", 1., float, "per-side aggressive order arrival probability per tick"),
        ("max-market-order-levels", 0, int, "maximum book levels consumed per aggressive order; 0=uncapped"),
        ("scan-interval-seconds", 60., float, "strategy entry scan cadence, separate from tick cadence"),
    ]:
        profiled = {"x", "n", "a", "b", "c", "d", "base-quantity", "cancellation-probability",
                    "market-order-scale", "market-order-probability", "max-market-order-levels"}
        parser.add_argument("--fake-" + name, default=None if name in profiled else default,
                            type=kind, help=help_text)
    parser.add_argument("--fake-no-viewer", action="store_true", help="omit per-day HTML viewer")


def config_from_args(args: argparse.Namespace) -> MarketConfig:
    cfg = ordinary_bull_config() if args.fake_profile == "ordinary-bull" else MarketConfig()
    overrides = {name: value for name in MarketConfig.__dataclass_fields__
                 if name != "profile" and (value := getattr(args, "fake_" + name, None)) is not None}
    return replace(cfg, **overrides)


def prepare_market_args(args: argparse.Namespace, remaining: list[str]) -> list[str]:
    if args.market_source == "real":
        return remaining
    # Synthetic data may never silently borrow a live DB, fills, capital or selected signals.
    conflicts = {"--db", "--logs-dir", "--use-selected-signals", "--use-actual-fill-exits", "--trend-filter-enabled"}
    for token in remaining:
        if token.split("=", 1)[0] in conflicts:
            raise ValueError(f"{token} cannot be combined with --market-source fake")
    run_dir = Path(args.fake_output_dir) if args.fake_output_dir else Path(
        "Daily_bot/backtest/results/fake_market"
    ) / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    generate_market(run_dir, config_from_args(args), viewer=not args.fake_no_viewer)
    from Daily_bot.backtest.replay_db_builder import build_replay_db_from_logs
    db = build_replay_db_from_logs(run_dir, run_dir / "replay.sqlite3", market_pattern="fake_market_traces_*.csv")
    injected = ["--db", str(db), "--ignore-selected-signals", "--ignore-actual-fill-exits", "--trend-filter-disabled"]
    if not any(t.split("=", 1)[0] == "--out" for t in remaining):
        injected += ["--out", str(run_dir / "backtest_fake.csv")]
    return [*injected, *remaining]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_market_arguments(parser)
    args = parser.parse_args()
    cfg = config_from_args(args)
    path = Path(args.fake_output_dir) if args.fake_output_dir else Path(
        "Daily_bot/backtest/results/fake_market") / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    generate_market(path, cfg, viewer=not args.fake_no_viewer)


if __name__ == "__main__":
    main()
