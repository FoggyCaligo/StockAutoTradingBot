from __future__ import annotations

import argparse
import time
from pathlib import Path

from broker.kiwoom_client import KiwoomClient
from broker.mock_client import MockKiwoomClient
from models import BotState, Candidate
from risk.force_sell import force_sell
from risk.guards import has_open_orders, has_position, trim_targets, calc_order_quantity
from storage.db import Recorder
from strategy.orderbook_predictor import calc_target_sell_price
from strategy.signal import calc_expected_return, final_filter, get_candidates_top
from strategy.universe import UniverseConfig, get_candidates
from utils import RateLimiter, is_after_now, is_between_now, load_yaml
from dotenv import load_dotenv

load_dotenv()


def build_client(dry_run: bool):
    return MockKiwoomClient() if dry_run else KiwoomClient()


def scan_and_rank(client, recorder: Recorder, cfg: dict) -> list[Candidate]:
    universe_cfg = UniverseConfig(
        min_price=cfg["universe"]["min_price"],
        max_price=cfg["universe"]["max_price"],
        min_market_cap_krw=cfg["universe"]["min_market_cap_krw"],
        min_trading_value_krw=cfg["universe"]["min_trading_value_krw"],
        csv_path=cfg["universe"].get("csv_path"),
    )

    candidates = get_candidates(universe_cfg, cfg["trend_filter"]["enabled"])
    limiter = RateLimiter(cfg["api"]["quote_rate_limit_per_second"])
    calculated: list[Candidate] = []

    for ticker, candidate in candidates.items():
        limiter.wait()
        snapshot = client.get_20hoga(ticker)
        candidate = calc_expected_return(candidate, snapshot)
        recorder.save_snapshot(candidate, snapshot)
        recorder.save_signal(candidate, selected=False)
        calculated.append(candidate)

    return calculated


def activate_buy(client, recorder: Recorder, targets: list[Candidate], cfg: dict) -> None:
    order_limiter = RateLimiter(cfg["api"]["order_rate_limit_per_second"])
    budget_per_stock = cfg["risk"]["max_budget_per_stock_krw"]
    budget_per_cycle = cfg["risk"].get("max_budget_per_cycle_krw", 0)
    tick_offset = cfg["strategy"]["sell_tick_offset"]
    try:
        orderable_cash = client.get_orderable_cash()
    except Exception as exc:
        print(f"Failed to fetch orderable cash: {exc}")
        return

    if orderable_cash <= 0:
        print("Orderable cash is zero. Skip buy cycle.")
        return

    remaining_cash = min(orderable_cash, budget_per_cycle) if budget_per_cycle > 0 else orderable_cash

    for candidate in targets:
        per_stock_budget = min(budget_per_stock, remaining_cash)
        qty = calc_order_quantity(candidate, per_stock_budget)
        if qty <= 0:
            continue

        estimated_cost = qty * candidate.price
        if estimated_cost <= 0 or estimated_cost > remaining_cash:
            continue

        order_limiter.wait()
        buy_order = client.buy_limit(candidate.ticker, qty, candidate.price)
        recorder.save_order(buy_order)
        remaining_cash -= estimated_cost

        fill = client.wait_buy_filled(buy_order.order_id)
        if fill is None:
            # TODO: cancel buy order when real client implementation exists.
            continue

        target_price = calc_target_sell_price(candidate.expect_price, tick_offset)
        if target_price <= fill.price:
            order_limiter.wait()
            sell_order = client.sell_market(candidate.ticker, fill.quantity)
            recorder.save_order(sell_order)
            continue

        order_limiter.wait()
        sell_order = client.sell_limit(candidate.ticker, fill.quantity, target_price)
        recorder.save_order(sell_order)

        if remaining_cash < candidate.price:
            break


def run(cfg_path: str, dry_run_override: bool | None = None) -> None:
    cfg = load_yaml(cfg_path)
    dry_run = cfg["risk"]["dry_run"] if dry_run_override is None else dry_run_override

    client = build_client(dry_run)
    recorder = Recorder(Path("bot.sqlite3"))
    client.auth()

    state = BotState.NO_POSITION
    force_sell_done = False

    while True:
        if is_after_now(cfg["market"]["force_sell_time"]) and not force_sell_done:
            state = BotState.FORCE_SELLING
            force_sell(client)
            force_sell_done = True
            state = BotState.STOPPED
            print("Force sell completed. Stop trading for today.")
            break

        if not is_between_now(cfg["market"]["start_buy_time"], cfg["market"]["stop_buy_time"]):
            time.sleep(5)
            continue

        positions = client.get_positions()
        open_orders = client.get_open_orders()

        if has_position(positions) or has_open_orders(open_orders):
            time.sleep(5)
            continue

        state = BotState.SCANNING
        calculated = scan_and_rank(client, recorder, cfg)
        top = get_candidates_top(calculated, cfg["strategy"]["top_ratio"])
        filtered = final_filter(
            top,
            cfg["strategy"]["min_expected_return_percent"],
            cfg["strategy"]["max_spread_percent"],
        )
        targets = trim_targets(
            filtered,
            cfg["strategy"]["max_buy_count"],
            cfg["risk"]["max_budget_per_stock_krw"],
            cfg["strategy"]["sell_tick_offset"],
        )

        for target in targets:
            recorder.save_signal(target, selected=True)

        if targets:
            state = BotState.BUYING
            activate_buy(client, recorder, targets, cfg)
            state = BotState.SELLING

        time.sleep(cfg["strategy"]["scan_interval_seconds"])


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Use mock broker and do not send real orders")
    parser.add_argument("--real", action="store_true", help="Use real Kiwoom client. Requires implementation and credentials")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    override = True if args.dry_run else False if args.real else None
    run(args.config, override)
