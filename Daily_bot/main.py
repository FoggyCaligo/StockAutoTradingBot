from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from dotenv import load_dotenv

from Daily_bot.broker.kiwoom_client import KiwoomClient
from Daily_bot.broker.mock_client import MockKiwoomClient
from Daily_bot.models import BotState, Candidate
from Daily_bot.risk.force_sell import force_sell
from Daily_bot.risk.guards import calc_order_quantity, has_open_orders, has_position, trim_targets
from Daily_bot.risk.stop_loss import monitor_stop_loss
from Daily_bot.storage.db import Recorder
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price
from Daily_bot.strategy.signal import calc_expected_return, final_filter, get_candidates_top
from Daily_bot.strategy.universe import UniverseConfig, get_candidates
from Daily_bot.utils import RateLimiter, is_after_now, is_between_now, load_yaml

load_dotenv()


def build_client(dry_run: bool):
    return MockKiwoomClient() if dry_run else KiwoomClient()


def build_universe_config(cfg: dict) -> UniverseConfig:
    return UniverseConfig(
        min_market_cap_krw=cfg["universe"]["min_market_cap_krw"],
        min_trading_value_krw=cfg["universe"]["min_trading_value_krw"],
        csv_path=cfg["universe"].get("csv_path"),
        cache_path=cfg["universe"].get("cache_path"),
        source=cfg["universe"].get("source", "KOSPI200"),
        refresh_daily=cfg["universe"].get("refresh_daily", True),
    )


def warm_universe(cfg: dict) -> None:
    get_candidates(build_universe_config(cfg), cfg["trend_filter"]["enabled"])


def scan_and_rank(client, recorder: Recorder, cfg: dict) -> list[Candidate]:
    candidates = get_candidates(build_universe_config(cfg), cfg["trend_filter"]["enabled"])
    limiter = RateLimiter(cfg["api"]["quote_rate_limit_per_second"])
    calculated: list[Candidate] = []
    for ticker, candidate in candidates.items():
        try:
            limiter.wait()
            snapshot = client.get_20hoga(ticker)
            candidate = calc_expected_return(candidate, snapshot, cfg["strategy"]["sell_tick_offset"])
            recorder.save_snapshot(candidate, snapshot)
            recorder.save_signal(candidate, selected=False)
            calculated.append(candidate)
        except Exception as exc:
            print(f"Skipping {ticker} during scan due to error: {exc}")
    return calculated


def cancel_unfilled_buy(client, buy_order, candidate: Candidate, qty: int, recorder: Recorder) -> None:
    if not buy_order.order_id:
        return
    client.cancel_order(buy_order.order_id, ticker=candidate.ticker, quantity=qty)
    if hasattr(client, "wait_until_order_cancelled"):
        client.wait_until_order_cancelled(buy_order.order_id)


def _resolve_buy_fill_price(fill, buy_limit_price: int, ticker: str) -> int:
    if buy_limit_price > 0 and fill.price > buy_limit_price:
        print(
            f"Fill price anomaly for {ticker}: fill_price={fill.price} exceeds "
            f"buy_limit_price={buy_limit_price}. Using buy_limit_price for exit decision. raw_fill={fill.raw}"
        )
        return buy_limit_price
    return fill.price


def submit_exit_order(client, recorder: Recorder, candidate: Candidate, fill, tick_offset: int, order_limiter: RateLimiter) -> None:
    target_price = calc_target_sell_price(candidate.expect_price, tick_offset)
    decision_fill_price = _resolve_buy_fill_price(fill, candidate.price, candidate.ticker)
    if target_price <= decision_fill_price:
        print(
            f"Submitting market sell for {candidate.ticker}: "
            f"target_price={target_price} decision_fill_price={decision_fill_price} raw_fill={fill.raw}"
        )
        order_limiter.wait()
        recorder.save_order(client.sell_market(candidate.ticker, fill.quantity))
        return
    print(
        f"Submitting limit sell for {candidate.ticker}: "
        f"target_price={target_price} decision_fill_price={decision_fill_price} raw_fill={fill.raw}"
    )
    order_limiter.wait()
    recorder.save_order(client.sell_limit(candidate.ticker, fill.quantity, target_price))


def _refresh_remaining_cash(client, budget_per_cycle: int) -> int | None:
    try:
        refreshed_cash = client.get_orderable_cash()
    except Exception as exc:
        print(f"Failed to refresh orderable cash: {exc}")
        return None
    if refreshed_cash <= 0:
        return 0
    return min(refreshed_cash, budget_per_cycle) if budget_per_cycle > 0 else refreshed_cash


def _recheck_account_state(client) -> tuple[list, list[dict]] | tuple[None, None]:
    try:
        return client.get_positions(), client.get_open_orders()
    except Exception as exc:
        print(f"Failed to recheck account state after buy error: {exc}")
        return None, None


def _ticker_key(ticker: str) -> str:
    return str(ticker or "").strip().upper().removeprefix("A")


def _find_position_for_candidate(positions: list, candidate: Candidate):
    candidate_key = _ticker_key(candidate.ticker)
    for position in positions:
        if _ticker_key(getattr(position, "ticker", "")) == candidate_key and getattr(position, "quantity", 0) > 0:
            return position
    return None


def submit_target_exit_order_from_position_if_present(
    client,
    recorder: Recorder,
    candidate: Candidate,
    tick_offset: int,
    order_limiter: RateLimiter,
) -> bool:
    try:
        position = _find_position_for_candidate(client.get_positions(), candidate)
    except Exception as exc:
        print(f"Failed to recheck positions for target exit recovery: {exc}")
        return False
    if position is None:
        return False
    quantity = getattr(position, "quantity", 0)
    if quantity <= 0:
        return False
    target_price = calc_target_sell_price(candidate.expect_price, tick_offset)
    print(
        f"Position recovered for {candidate.ticker} after fill lookup failed: "
        f"quantity={quantity} target_price={target_price}. Submitting target limit sell."
    )
    order_limiter.wait()
    recorder.save_order(client.sell_limit(candidate.ticker, quantity, target_price))
    return True


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

    for index, candidate in enumerate(targets):
        try:
            remaining_target_count = len(targets) - index
            per_stock_budget = min(budget_per_stock, remaining_cash) if budget_per_stock > 0 else (
                remaining_cash if remaining_target_count <= 1 else remaining_cash // remaining_target_count
            )
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

            fill = client.wait_buy_filled(buy_order.order_id, qty)
            if fill is None:
                order_limiter.wait()
                cancel_unfilled_buy(client, buy_order, candidate, qty, recorder)
                partial_fill = client.get_buy_fill(buy_order.order_id) if hasattr(client, "get_buy_fill") else None
                if partial_fill is not None and partial_fill.quantity > 0:
                    print(
                        f"Partial fill recovered after cancel for {candidate.ticker}: "
                        f"{partial_fill.quantity} shares at fill_price={partial_fill.price}. raw_fill={partial_fill.raw}."
                    )
                    submit_exit_order(client, recorder, candidate, partial_fill, tick_offset, order_limiter)
                    return
                if submit_target_exit_order_from_position_if_present(client, recorder, candidate, tick_offset, order_limiter):
                    return
                refreshed_cash = _refresh_remaining_cash(client, budget_per_cycle)
                if refreshed_cash is None:
                    return
                remaining_cash = refreshed_cash
                continue

            submit_exit_order(client, recorder, candidate, fill, tick_offset, order_limiter)
            if remaining_cash < candidate.price:
                break
        except Exception as exc:
            print(f"Buy flow error for {candidate.ticker}: {exc}")
            positions, open_orders = _recheck_account_state(client)
            if positions is None or open_orders is None:
                print("Stopping new buys because account state could not be confirmed.")
                return
            print("Stopping new buys after buy error. " f"positions={len(positions)} open_orders={len(open_orders)}")
            if has_position(positions):
                print("Position detected after buy error. Manual check recommended before resuming.")
            if has_open_orders(open_orders):
                print("Open orders detected after buy error. Manual check recommended before resuming.")
            return


def run(cfg_path: str, dry_run_override: bool | None = None) -> None:
    cfg = load_yaml(cfg_path)
    dry_run = cfg["risk"]["dry_run"] if dry_run_override is None else dry_run_override
    client = build_client(dry_run)
    recorder = Recorder(Path("bot.sqlite3"))
    client.auth()
    state = BotState.NO_POSITION
    force_sell_done = False
    warmed_session = False

    while True:
        if is_after_now(cfg["market"]["force_sell_time"]) and not force_sell_done:
            state = BotState.FORCE_SELLING
            force_sell(client)
            force_sell_done = True
            state = BotState.STOPPED
            print("Force sell completed. Stop trading for today.")
            break

        if is_between_now(cfg["market"].get("prewarm_start_time", cfg["market"]["start_buy_time"]), cfg["market"]["start_buy_time"]) and not warmed_session:
            try:
                warm_universe(cfg)
                warmed_session = True
                print("Universe warm-up completed.")
            except Exception as exc:
                print(f"Universe warm-up failed: {exc}")

        if not is_between_now(cfg["market"]["start_buy_time"], cfg["market"]["stop_buy_time"]):
            time.sleep(5)
            continue

        positions = client.get_positions()
        open_orders = client.get_open_orders()
        if has_position(positions):
            stop_loss_executed = monitor_stop_loss(client, recorder, positions, open_orders, cfg)
            if stop_loss_executed:
                time.sleep(1)
                continue
        if has_position(positions) or has_open_orders(open_orders):
            time.sleep(5)
            continue

        state = BotState.SCANNING
        try:
            calculated = scan_and_rank(client, recorder, cfg)
        except Exception as exc:
            print(f"Scan cycle failed: {exc}")
            time.sleep(cfg["strategy"]["scan_interval_seconds"])
            continue
        top = get_candidates_top(calculated, cfg["strategy"]["top_ratio"])
        filtered = final_filter(top, cfg["strategy"]["min_expected_return_percent"], cfg["strategy"]["sell_tick_offset"])
        targets = trim_targets(filtered, cfg["strategy"]["max_buy_count"], cfg["risk"]["max_budget_per_stock_krw"], cfg["strategy"]["sell_tick_offset"])
        for target in targets:
            recorder.save_signal(target, selected=True)
        if targets:
            state = BotState.BUYING
            activate_buy(client, recorder, targets, cfg)
            state = BotState.SELLING
        time.sleep(cfg["strategy"]["scan_interval_seconds"])


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config/settings.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="Use mock broker and do not send real orders")
    parser.add_argument("--real", action="store_true", help="Use real Kiwoom client. Requires implementation and credentials")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    override = True if args.dry_run else False if args.real else None
    run(args.config, override)
