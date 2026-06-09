from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from dotenv import load_dotenv

from Daily_bot.broker.kiwoom_client import KiwoomClient
from Daily_bot.broker.mock_client import MockKiwoomClient
from Daily_bot.models import BotState, Candidate, Fill
from Daily_bot.risk.force_sell import force_sell
from Daily_bot.risk.guards import calc_order_quantity, has_open_orders, has_position, select_affordable_targets
from Daily_bot.risk.stop_loss import monitor_stop_loss
from Daily_bot.storage.db import Recorder
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price
from Daily_bot.strategy.signal import calc_expected_return, final_filter, get_candidates_top
from Daily_bot.strategy.universe import UniverseConfig, get_candidates
from Daily_bot.telemetry.trace_helpers import trace_candidate_watchlist
from Daily_bot.utils import RateLimiter, get_tick_size, is_after_now, is_between_now, load_yaml, round_to_tick

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


def resolve_target_budget_per_stock(cfg: dict, planning_cash: int) -> int:
    if planning_cash <= 0:
        return 0

    ratio = float(cfg["risk"].get("target_budget_ratio_per_stock", 0) or 0)
    budget_from_ratio = int(planning_cash * ratio) if ratio > 0 else 0
    max_budget_per_stock = int(cfg["risk"].get("max_budget_per_stock_krw", 0) or 0)

    if max_budget_per_stock > 0 and budget_from_ratio > 0:
        return min(budget_from_ratio, max_budget_per_stock)
    if budget_from_ratio > 0:
        return budget_from_ratio
    if max_budget_per_stock > 0:
        return max_budget_per_stock
    return 0


def _normalize_local_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _parse_cash_flow_effective_at(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _normalize_local_datetime(parsed)


def get_external_cash_flow_since(cfg: dict, since: datetime, until: datetime | None = None) -> int:
    accounting_cfg = cfg.get("accounting", {}) if isinstance(cfg, dict) else {}
    cash_flows = accounting_cfg.get("cash_flows", []) or []
    start = _normalize_local_datetime(since)
    end = _normalize_local_datetime(until or datetime.now())
    total = 0

    for item in cash_flows:
        if not isinstance(item, dict):
            continue
        effective_at = _parse_cash_flow_effective_at(item.get("effective_at") or item.get("date"))
        if effective_at is None or effective_at <= start or effective_at > end:
            continue
        try:
            total += int(item.get("amount_krw", item.get("amount", 0)) or 0)
        except (TypeError, ValueError):
            continue

    return total


def resolve_buy_count(cfg: dict, empty_slots: int, planning_cash: int) -> int:
    configured_buy_count = int(cfg["strategy"].get("max_buy_count", 0) or 0)
    slot_limited_count = empty_slots if configured_buy_count <= 0 else min(configured_buy_count, empty_slots)
    if slot_limited_count <= 0:
        return 0

    min_slot_count = max(1, int(cfg["risk"].get("min_slot_count", 1) or 1))
    target_budget_per_stock = resolve_target_budget_per_stock(cfg, planning_cash)
    if target_budget_per_stock <= 0:
        return slot_limited_count

    affordable_count = max(1, planning_cash // target_budget_per_stock) if planning_cash > 0 else 0
    desired_count = max(min_slot_count, affordable_count)
    return min(slot_limited_count, desired_count)


def resolve_empty_slots(max_position_count: int, active_count: int, candidate_count: int = 0) -> int:
    if max_position_count <= 0:
        return max(candidate_count, 0)
    return max(0, max_position_count - active_count)


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
            recorder.save_market_trace(candidate, snapshot, phase="scan_candidate", selected=False, reason="main_scan")
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


def _min_sell_price_above_buy(buy_price: int) -> int:
    if buy_price <= 0:
        return 0
    return round_to_tick(buy_price + get_tick_size(buy_price))


def _safe_target_sell_price(candidate: Candidate, tick_offset: int, buy_reference_price: int) -> int:
    target_price = calc_target_sell_price(candidate.expect_price, tick_offset)
    min_sell_price = _min_sell_price_above_buy(buy_reference_price)
    if min_sell_price > 0 and target_price < min_sell_price:
        print(
            f"Raising target sell price for {candidate.ticker}: "
            f"raw_target_price={target_price} buy_reference_price={buy_reference_price} "
            f"safe_target_price={min_sell_price}"
        )
        return min_sell_price
    return target_price


def _record_fill_safely(client, recorder: Recorder, order_id: str, side: str, source: str) -> bool:
    if not order_id or not hasattr(client, "get_order_fill"):
        return False
    try:
        fill = client.get_order_fill(order_id)
        if fill:
            recorder.save_fill(fill, side=side, source=source)
            return True
    except Exception as exc:
        print(f"Warning: Failed to record immediate fill for {side} order {order_id} ({source}): {exc}")
    return False


def _poll_fill_until_recorded(
    client,
    recorder: Recorder,
    order_id: str,
    side: str,
    source: str,
    timeout_seconds: int = 10,
    poll_seconds: float = 1.0,
) -> None:
    if not order_id or not hasattr(client, "get_order_fill"):
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            fill = client.get_order_fill(order_id)
        except Exception as exc:
            print(f"Warning: Failed to poll fill for {side} order {order_id} ({source}): {exc}")
            return
        if fill:
            recorder.save_fill(fill, side=side, source=source)
            return
        time.sleep(poll_seconds)


def submit_exit_order(client, recorder: Recorder, candidate: Candidate, fill, tick_offset: int, order_limiter: RateLimiter) -> None:
    decision_fill_price = _resolve_buy_fill_price(fill, candidate.price, candidate.ticker)
    target_price = _safe_target_sell_price(candidate, tick_offset, decision_fill_price)
    print(
        f"Submitting limit sell for {candidate.ticker}: "
        f"target_price={target_price} decision_fill_price={decision_fill_price} raw_fill={fill.raw}"
    )
    order_limiter.wait()
    sell_order = client.sell_limit(candidate.ticker, fill.quantity, target_price)
    recorder.save_order(sell_order)
    if not _record_fill_safely(client, recorder, sell_order.order_id, "SELL", "target_exit"):
        _poll_fill_until_recorded(client, recorder, sell_order.order_id, "SELL", "target_exit_safety_poll")


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


def _get_open_order_ticker(order: dict) -> str:
    return _ticker_key(order.get("ticker") or order.get("stk_cd") or order.get("pdno") or "")


def _get_active_tickers(positions: list, open_orders: list[dict]) -> set[str]:
    tickers = {
        _ticker_key(getattr(position, "ticker", ""))
        for position in positions
        if getattr(position, "quantity", 0) > 0
    }
    tickers.update(ticker for ticker in (_get_open_order_ticker(order) for order in open_orders) if ticker)
    return tickers


def _find_position_for_candidate(positions: list, candidate: Candidate):
    candidate_key = _ticker_key(candidate.ticker)
    for position in positions:
        if _ticker_key(getattr(position, "ticker", "")) == candidate_key and getattr(position, "quantity", 0) > 0:
            return position
    return None


def _estimate_position_value(client, position) -> int:
    quantity = getattr(position, "quantity", 0)
    if quantity <= 0:
        return 0
    fallback_price = getattr(position, "avg_price", 0)
    try:
        snapshot = client.get_20hoga(getattr(position, "ticker", ""))
        current_price = snapshot.current_price or fallback_price
    except Exception as exc:
        print(f"Failed to price position {getattr(position, 'ticker', '')}: {exc}. Falling back to avg_price.")
        current_price = fallback_price
    return max(current_price, 0) * quantity


def _to_int(value: object) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _estimate_open_order_value(order: dict) -> int:
    quantity = _to_int(
        order.get("oso_qty")
        or order.get("remaining_qty")
        or order.get("rmn_qty")
        or order.get("ord_qty")
    )
    if quantity <= 0:
        return 0

    side = str(order.get("io_tp_nm") or order.get("side") or "").strip().upper()
    if "SELL" in side or "매도" in side:
        return 0

    price = _to_int(order.get("ord_pric") or order.get("price") or order.get("unit_cntr_pric"))
    if price <= 0:
        return 0
    return quantity * price


def estimate_account_value(client, positions: list | None = None, open_orders: list[dict] | None = None) -> int:
    try:
        cash = client.get_orderable_cash()
    except Exception as exc:
        print(f"Failed to fetch cash for account value estimate: {exc}")
        cash = 0

    if positions is None:
        try:
            positions = client.get_positions()
        except Exception as exc:
            print(f"Failed to fetch positions for account value estimate: {exc}")
            positions = []

    if open_orders is None:
        try:
            open_orders = client.get_open_orders()
        except Exception as exc:
            print(f"Failed to fetch open orders for account value estimate: {exc}")
            open_orders = []

    position_value = sum(_estimate_position_value(client, position) for position in positions)
    open_buy_order_value = sum(_estimate_open_order_value(order) for order in open_orders)
    return max(cash, 0) + position_value + open_buy_order_value


def is_daily_loss_limit_reached(
    client,
    cfg: dict,
    initial_account_value: int,
    session_started_at: datetime | None = None,
    positions: list | None = None,
    open_orders: list[dict] | None = None,
    recorder: Recorder | None = None,
) -> bool:
    limit_percent = float(cfg["risk"].get("daily_loss_limit_percent", 0) or 0)
    if limit_percent <= 0 or initial_account_value <= 0:
        return False

    current_value = estimate_account_value(client, positions, open_orders)
    external_cash_flow = get_external_cash_flow_since(cfg, session_started_at) if session_started_at is not None else 0
    adjusted_current_value = current_value - external_cash_flow
    adjusted_pnl = adjusted_current_value - initial_account_value
    loss_percent = (initial_account_value - adjusted_current_value) / initial_account_value * 100

    if recorder is not None:
        try:
            cash = client.get_orderable_cash()
        except Exception as exc:
            print(f"Failed to fetch cash for account trace: {exc}")
            cash = 0
        recorder.save_account_trace(
            phase="daily_loss_check",
            cash=cash,
            account_value=current_value,
            positions=positions or [],
            open_orders=open_orders or [],
            external_cash_flow=external_cash_flow,
            adjusted_account_value=adjusted_current_value,
            adjusted_pnl=adjusted_pnl,
            loss_percent=loss_percent,
        )

    if loss_percent >= limit_percent:
        print(
            f"Daily loss limit reached: initial={initial_account_value} "
            f"current={current_value} adjusted_current={adjusted_current_value} "
            f"external_cash_flow={external_cash_flow} loss_percent={loss_percent:.2f}% "
            f"limit={limit_percent:.2f}%. Blocking new buys only."
        )
        return True
    return False


def poll_and_record_new_fills(client, recorder: Recorder) -> None:
    if not hasattr(client, "get_order_fill"):
        return
    for order in recorder.get_orders_needing_fill_poll():
        order_id = str(order.get("broker_order_id") or "").strip()
        side = str(order.get("side") or "").strip().upper()
        already_recorded = int(order.get("recorded_fill_quantity") or 0)
        if not order_id or side not in {"BUY", "SELL"}:
            continue
        try:
            fill = client.get_order_fill(order_id)
        except Exception as exc:
            print(f"Failed to poll fill for order_id={order_id} ticker={order.get('ticker')}: {exc}")
            continue
        if fill is None or fill.quantity <= already_recorded:
            continue
        delta_quantity = fill.quantity - already_recorded
        delta_fill = Fill(
            order_id=fill.order_id,
            ticker=fill.ticker,
            quantity=delta_quantity,
            price=fill.price,
            filled_at=fill.filled_at,
            raw={"source": "delta_poll", "total_fill_quantity": fill.quantity, "raw": fill.raw},
        )
        recorder.save_fill(delta_fill, side=side, source="poll")


def submit_target_exit_order_from_position_if_present(
    client,
    recorder: Recorder,
    candidate: Candidate,
    buy_order_id: str,
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
    buy_reference_price = getattr(position, "avg_price", 0) or candidate.price
    recovered_fill = Fill(
        order_id=buy_order_id,
        ticker=candidate.ticker,
        quantity=quantity,
        price=buy_reference_price,
        raw={"source": "position_recovery"},
    )
    recorder.save_fill(recovered_fill, side="BUY", source="position_recovery")
    target_price = _safe_target_sell_price(candidate, tick_offset, buy_reference_price)
    print(
        f"Position recovered for {candidate.ticker} after fill lookup failed: "
        f"quantity={quantity} target_price={target_price}. Submitting target limit sell."
    )
    order_limiter.wait()
    sell_order = client.sell_limit(candidate.ticker, quantity, target_price)
    recorder.save_order(sell_order)
    if not _record_fill_safely(client, recorder, sell_order.order_id, "SELL", "target_exit_recovery"):
        _poll_fill_until_recorded(client, recorder, sell_order.order_id, "SELL", "target_exit_recovery_safety_poll")
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
                    recorder.save_fill(partial_fill, side="BUY", source="partial_after_cancel")
                    print(
                        f"Partial fill recovered after cancel for {candidate.ticker}: "
                        f"{partial_fill.quantity} shares at fill_price={partial_fill.price}. raw_fill={partial_fill.raw}."
                    )
                    submit_exit_order(client, recorder, candidate, partial_fill, tick_offset, order_limiter)
                    return
                if submit_target_exit_order_from_position_if_present(
                    client,
                    recorder,
                    candidate,
                    buy_order.order_id,
                    tick_offset,
                    order_limiter,
                ):
                    return
                refreshed_cash = _refresh_remaining_cash(client, budget_per_cycle)
                if refreshed_cash is None:
                    return
                remaining_cash = refreshed_cash
                continue

            recorder.save_fill(fill, side="BUY", source="wait_buy_filled")
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
    watchlist: dict[str, Candidate] = {}
    session_started_at = datetime.now()
    initial_account_value = estimate_account_value(client)
    print(f"Initial account value estimate: {initial_account_value}")

    while True:
        if is_after_now(cfg["market"]["force_sell_time"]) and not force_sell_done:
            state = BotState.FORCE_SELLING
            force_sell(client, recorder=recorder)
            poll_and_record_new_fills(client, recorder)
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
        active_tickers = _get_active_tickers(positions, open_orders)
        poll_and_record_new_fills(client, recorder)
        if watchlist:
            watchlist = trace_candidate_watchlist(
                client=client,
                recorder=recorder,
                candidates=watchlist,
                quote_rate_limit_per_second=cfg["api"]["quote_rate_limit_per_second"],
                sell_tick_offset=cfg["strategy"]["sell_tick_offset"],
                selected_keys=active_tickers,
            )
        if has_position(positions):
            stop_loss_executed = monitor_stop_loss(client, recorder, positions, open_orders, cfg)
            poll_and_record_new_fills(client, recorder)
            if stop_loss_executed:
                time.sleep(1)
                continue

        if is_daily_loss_limit_reached(
            client,
            cfg,
            initial_account_value,
            session_started_at,
            positions,
            open_orders,
            recorder=recorder,
        ):
            time.sleep(5)
            continue

        max_position_count = int(cfg["risk"].get("max_position_count", cfg["strategy"]["max_buy_count"]) or 0)
        if max_position_count > 0 and len(active_tickers) >= max_position_count:
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
        filtered = final_filter(
            top,
            cfg["strategy"]["min_expected_return_percent"],
            cfg["strategy"]["sell_tick_offset"],
            cfg["strategy"].get("max_spread_percent", 0.7),
        )
        filtered = [candidate for candidate in filtered if _ticker_key(candidate.ticker) not in active_tickers]
        for candidate in filtered:
            watchlist[_ticker_key(candidate.ticker)] = candidate
        empty_slots = resolve_empty_slots(max_position_count, len(active_tickers), len(filtered))
        if empty_slots <= 0:
            time.sleep(cfg["strategy"]["scan_interval_seconds"])
            continue
        try:
            orderable_cash = client.get_orderable_cash()
        except Exception as exc:
            print(f"Failed to fetch orderable cash for target planning: {exc}")
            time.sleep(cfg["strategy"]["scan_interval_seconds"])
            continue
        planning_cash = min(orderable_cash, cfg["risk"].get("max_budget_per_cycle_krw", 0)) if cfg["risk"].get("max_budget_per_cycle_krw", 0) > 0 else orderable_cash
        buy_count = resolve_buy_count(cfg, empty_slots, planning_cash)
        if buy_count <= 0:
            time.sleep(cfg["strategy"]["scan_interval_seconds"])
            continue
        targets = select_affordable_targets(
            filtered,
            buy_count,
            planning_cash,
            cfg["risk"]["max_budget_per_stock_krw"],
            cfg["strategy"]["sell_tick_offset"],
        )
        if targets and len(targets) < buy_count:
            print(f"Planned {len(targets)} affordable targets out of desired {buy_count} due to cash constraints.")
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
