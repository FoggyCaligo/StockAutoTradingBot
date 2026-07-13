from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
BOT_DB_PATH = ROOT / "bot.sqlite3"
BOT_LOG_DIR = ROOT / "logs"
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
from Daily_bot.strategy.universe import UniverseConfig, get_candidates, get_kospi_change_percent
from Daily_bot.telemetry.trace_helpers import trace_active_positions, trace_candidate_watchlist
from Daily_bot.utils import (
    RateLimiter,
    ceil_tick_count,
    count_ticks_between_prices,
    get_tick_size,
    is_after_now,
    is_between_now,
    load_yaml,
    move_price_by_ticks,
    round_to_tick,
)

load_dotenv()


def build_client(dry_run: bool):
    return MockKiwoomClient() if dry_run else KiwoomClient()


def build_universe_config(cfg: dict) -> UniverseConfig:
    return UniverseConfig(
        min_market_cap_krw=cfg["universe"]["min_market_cap_krw"],
        min_trading_value_krw=cfg["universe"]["min_trading_value_krw"],
        csv_path=cfg["universe"].get("csv_path"),
        cache_path=cfg["universe"].get("cache_path"),
        source=cfg["universe"].get("source", "KOSPI"),
        refresh_daily=cfg["universe"].get("refresh_daily", True),
    )


def resolve_fallback_expected_return_thresholds(strategy_cfg: dict) -> list[float]:
    primary_threshold = float(strategy_cfg.get("min_expected_return_percent", 0.0) or 0.0)
    raw_thresholds = strategy_cfg.get("min_expected_return_fallback_percents")
    if raw_thresholds is None:
        raw_thresholds = strategy_cfg.get("min_expected_return_fallback_percent", 0.0)

    if isinstance(raw_thresholds, (list, tuple)):
        values = raw_thresholds
    else:
        values = [raw_thresholds]

    thresholds: list[float] = []
    for value in values:
        try:
            threshold = float(value or 0.0)
        except (TypeError, ValueError):
            continue
        if 0 < threshold < primary_threshold and threshold not in thresholds:
            thresholds.append(threshold)
    return thresholds


def resolve_target_budget_per_stock(cfg: dict, planning_cash: int) -> int:
    if planning_cash <= 0:
        return 0

    slot_budget_unit = int(cfg["risk"].get("slot_budget_unit_krw", 0) or 0)
    max_budget_per_stock = int(cfg["risk"].get("max_budget_per_stock_krw", 0) or 0)
    if slot_budget_unit > 0:
        max_slot_count = int(cfg["risk"].get("max_slot_count", 0) or 0)
        raw_slot_count = max(1, planning_cash // slot_budget_unit)
        slot_count = resolve_total_slot_count(cfg, planning_cash)
        if slot_count <= 0:
            return 0
        budget_from_slots = planning_cash // slot_count
        if max_slot_count > 0 and raw_slot_count > max_slot_count:
            return min(budget_from_slots, max_budget_per_stock) if max_budget_per_stock > 0 else budget_from_slots
        if max_budget_per_stock > 0:
            return min(budget_from_slots, max_budget_per_stock)
        return budget_from_slots

    ratio = float(cfg["risk"].get("target_budget_ratio_per_stock", 0) or 0)
    budget_from_ratio = int(planning_cash * ratio) if ratio > 0 else 0

    if max_budget_per_stock > 0 and budget_from_ratio > 0:
        return min(budget_from_ratio, max_budget_per_stock)
    if budget_from_ratio > 0:
        return budget_from_ratio
    if max_budget_per_stock > 0:
        return max_budget_per_stock
    return 0


def resolve_total_slot_count(cfg: dict, total_capital: int) -> int:
    if total_capital <= 0:
        return 0

    min_slot_count = max(1, int(cfg["risk"].get("min_slot_count", 1) or 1))
    max_slot_count = int(cfg["risk"].get("max_slot_count", 0) or 0)
    slot_budget_unit = int(cfg["risk"].get("slot_budget_unit_krw", 0) or 0)
    if slot_budget_unit > 0:
        slot_count = max(min_slot_count, total_capital // slot_budget_unit)
        if max_slot_count > 0:
            return min(slot_count, max_slot_count)
        return slot_count

    target_budget_per_stock = resolve_target_budget_per_stock(cfg, total_capital)
    if target_budget_per_stock <= 0:
        return min_slot_count

    affordable_count = max(1, total_capital // target_budget_per_stock)
    slot_count = max(min_slot_count, affordable_count)
    if max_slot_count > 0:
        return min(slot_count, max_slot_count)
    return slot_count


def resolve_position_limit(cfg: dict, slot_count: int) -> int:
    hard_limit = int(cfg["risk"].get("max_position_count", cfg["strategy"].get("max_buy_count", 0)) or 0)
    if hard_limit > 0 and slot_count > 0:
        return min(hard_limit, slot_count)
    if hard_limit > 0:
        return hard_limit
    return slot_count


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


def resolve_buy_count(
    cfg: dict,
    empty_slots: int,
    planning_cash: int,
    target_budget_per_stock: int | None = None,
) -> int:
    configured_buy_count = int(cfg["strategy"].get("max_buy_count", 0) or 0)
    slot_limited_count = empty_slots if configured_buy_count <= 0 else min(configured_buy_count, empty_slots)
    if slot_limited_count <= 0:
        return 0
    risk_cfg = cfg.get("risk", {}) if isinstance(cfg, dict) else {}
    slot_budget_unit = int(risk_cfg.get("slot_budget_unit_krw", 0) or 0)
    max_slot_count = int(risk_cfg.get("max_slot_count", 0) or 0)
    if slot_budget_unit > 0 or max_slot_count > 0:
        session_slot_limit = resolve_total_slot_count(cfg, planning_cash)
        if session_slot_limit > 0:
            slot_limited_count = min(slot_limited_count, session_slot_limit)
        if slot_limited_count <= 0:
            return 0

    per_stock_budget = (
        int(target_budget_per_stock)
        if target_budget_per_stock is not None
        else resolve_target_budget_per_stock(cfg, planning_cash)
    )
    if per_stock_budget <= 0:
        return slot_limited_count

    affordable_count = planning_cash // per_stock_budget if planning_cash > 0 else 0
    return min(slot_limited_count, max(0, affordable_count))


def resolve_empty_slots(max_position_count: int, active_count: int, candidate_count: int = 0) -> int:
    if max_position_count <= 0:
        return max(candidate_count, 0)
    return max(0, max_position_count - active_count)


def should_wait_for_full_batch_exit(active_count: int, allow_refill_empty_slots: bool = True) -> bool:
    # When refill is disabled, block new entries until the whole batch is flat.
    if allow_refill_empty_slots:
        return False
    return active_count > 0


def _attempt_startup_carryover_liquidation_safely(client, recorder: Recorder) -> tuple[bool, bool]:
    try:
        positions = client.get_positions()
        open_orders = client.get_open_orders()
    except Exception as exc:
        print(f"Startup carryover account check failed: {exc}")
        return False, True
    if not has_position(positions) and not has_open_orders(open_orders):
        return True, False
    try:
        force_sell(client, recorder=recorder)
        return True, False
    except Exception as exc:
        print(f"Startup carryover liquidation failed: {exc}")
        return False, True


def warm_universe(cfg: dict) -> None:
    get_candidates(build_universe_config(cfg), cfg["trend_filter"]["enabled"])


def record_session_prev_close_prices(recorder: Recorder, cfg: dict) -> dict[str, int]:
    candidates = get_candidates(build_universe_config(cfg), cfg["trend_filter"]["enabled"])
    recorder.save_daily_reference_prices(candidates, source="universe_startup")
    return recorder.get_daily_reference_prices()


def apply_recorded_prev_close_prices(candidates: dict[str, Candidate], prev_close_prices: dict[str, int]) -> dict[str, Candidate]:
    if not prev_close_prices:
        return candidates
    for candidate in candidates.values():
        prev_close_price = int(prev_close_prices.get(_ticker_key(candidate.ticker), 0) or 0)
        if prev_close_price > 0:
            candidate.prev_close_price = prev_close_price
    return candidates


def resolve_kospi_change_percent() -> float | None:
    try:
        return get_kospi_change_percent()
    except Exception as exc:
        print(f"Failed to resolve KOSPI change percent: {exc}")
        return None


def scan_and_rank(
    client,
    recorder: Recorder,
    cfg: dict,
    kospi_change_percent: float | None = None,
    prev_close_prices: dict[str, int] | None = None,
) -> list[Candidate]:
    # This loop is the single quote pass for the whole filtered universe.
    # Every scanned candidate is persisted here, so adding a second
    # "universe recording" pass would only duplicate API traffic.
    candidates = get_candidates(build_universe_config(cfg), cfg["trend_filter"]["enabled"])
    candidates = apply_recorded_prev_close_prices(candidates, prev_close_prices or {})
    limiter = RateLimiter(cfg["api"]["quote_rate_limit_per_second"])
    calculated: list[Candidate] = []
    scan_cycle_at = datetime.now()
    for ticker, candidate in candidates.items():
        try:
            limiter.wait()
            snapshot = client.get_20hoga(ticker)
            candidate = calc_expected_return(candidate, snapshot, cfg["strategy"]["sell_tick_offset"], cfg["strategy"])
            recorder.save_snapshot(candidate, snapshot, scan_cycle_at=scan_cycle_at)
            recorder.save_signal(candidate, selected=False, scan_cycle_at=scan_cycle_at)
            recorder.save_market_trace(
                candidate,
                snapshot,
                phase="scan_candidate",
                selected=False,
                reason="main_scan",
                scan_cycle_at=scan_cycle_at,
                kospi_change_percent=kospi_change_percent,
            )
            calculated.append(candidate)
        except Exception as exc:
            print(f"Skipping {ticker} during scan due to error: {exc}")
    return calculated


def filter_candidates_for_entry(
    calculated: list[Candidate],
    cfg: dict,
    previous_scan_prices: dict[str, int] | None = None,
    active_tickers: set[str] | None = None,
    blocked_tickers: set[str] | None = None,
    allow_refill_empty_slots: bool | None = None,
) -> tuple[list[Candidate], float]:
    active_ticker_keys = active_tickers or set()
    blocked_ticker_keys = blocked_tickers or set()
    prev_scan_prices = previous_scan_prices or {}
    strategy_cfg = cfg["strategy"]
    primary_threshold = float(strategy_cfg["min_expected_return_percent"])
    fallback_thresholds = resolve_fallback_expected_return_thresholds(strategy_cfg)
    refill_enabled = (
        bool(strategy_cfg.get("allow_refill_empty_slots", True))
        if allow_refill_empty_slots is None
        else bool(allow_refill_empty_slots)
    )

    top = get_candidates_top(calculated, strategy_cfg["top_ratio"])

    def _apply_threshold(min_expected_return_percent: float) -> list[Candidate]:
        filtered_candidates = final_filter(
            top,
            min_expected_return_percent,
            strategy_cfg["sell_tick_offset"],
            strategy_cfg.get("max_spread_percent", 0.7),
            strategy_cfg.get("min_prev_day_change_percent", 0.0),
            strategy_cfg.get("max_prev_day_change_percent", 15.0),
            strategy_cfg.get("spread_expected_return_multiplier", 0.0),
        )
        filtered_candidates = filter_candidates_by_prev_scan_jump(
            filtered_candidates,
            prev_scan_prices,
            strategy_cfg.get("max_intraday_jump_from_prev_scan_percent", 1.0),
        )
        return [
            candidate
            for candidate in filtered_candidates
            if _ticker_key(candidate.ticker) not in active_ticker_keys
            and _ticker_key(candidate.ticker) not in blocked_ticker_keys
        ]

    filtered = _apply_threshold(primary_threshold)
    used_threshold = primary_threshold

    fallback_allowed = refill_enabled or not active_ticker_keys
    if not filtered and fallback_allowed:
        for fallback_threshold in fallback_thresholds:
            fallback_filtered = _apply_threshold(fallback_threshold)
            if not fallback_filtered:
                continue
            print(
                "No entry candidates at primary expected-return threshold. "
                f"Retrying with fallback threshold {fallback_threshold:.2f} "
                f"instead of {primary_threshold:.2f} produced {len(fallback_filtered)} candidates."
            )
            filtered = fallback_filtered
            used_threshold = fallback_threshold
            break

    return filtered, used_threshold


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
    target_price = int(calc_target_sell_price(candidate.expect_price, tick_offset))
    min_sell_price = _min_sell_price_above_buy(buy_reference_price)
    if min_sell_price > 0 and target_price < min_sell_price:
        print(
            f"Raising target sell price for {candidate.ticker}: "
            f"raw_target_price={target_price} buy_reference_price={buy_reference_price} "
            f"safe_target_price={min_sell_price}"
        )
        return min_sell_price
    return target_price


def _resolve_stop_loss_tick_multiplier(cfg: dict | None = None) -> float:
    if not isinstance(cfg, dict):
        return 2.0
    value = cfg.get("risk", {}).get("stop_loss_tick_multiplier", 2.0)
    if value is None:
        return 2.0
    return float(value)


def _resolve_stop_loss_tick_count(cfg: dict | None = None) -> int:
    if not isinstance(cfg, dict):
        return 0
    value = cfg.get("risk", {}).get("stop_loss_tick_count", 0)
    if value is None:
        return 0
    return max(0, int(value))


def _build_exit_plan_metadata(
    candidate: Candidate,
    buy_reference_price: int,
    target_price: int,
    stop_loss_tick_count: int,
    stop_loss_tick_multiplier: float,
) -> dict[str, int | float]:
    expected_price = max(0, int(candidate.expect_price or 0))
    upward_ticks = count_ticks_between_prices(buy_reference_price, expected_price)
    stop_tick_distance = 0
    stop_loss_price = 0
    dynamic_tick_distance = 0
    if stop_loss_tick_multiplier > 0:
        dynamic_tick_distance = ceil_tick_count(upward_ticks * stop_loss_tick_multiplier)
        if dynamic_tick_distance <= 0:
            dynamic_tick_distance = 1
    stop_tick_distance = max(stop_loss_tick_count, dynamic_tick_distance)
    if stop_tick_distance > 0:
        stop_loss_price = move_price_by_ticks(buy_reference_price, -stop_tick_distance)
    return {
        "planned_entry_price": buy_reference_price,
        "planned_expect_price": expected_price,
        "planned_target_price": target_price,
        "planned_profit_tick_distance": upward_ticks,
        "planned_stop_loss_tick_distance": stop_tick_distance,
        "planned_stop_loss_tick_multiplier": stop_loss_tick_multiplier,
        "planned_stop_loss_price": stop_loss_price,
    }


def _record_fill_safely(client, recorder: Recorder, order_id: str, side: str, source: str) -> bool:
    if not order_id or not hasattr(client, "get_order_fill"):
        return False
    try:
        fill = _get_order_fill(client, order_id, side=side)
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
            fill = _get_order_fill(client, order_id, side=side)
        except Exception as exc:
            print(f"Warning: Failed to poll fill for {side} order {order_id} ({source}): {exc}")
            return
        if fill:
            recorder.save_fill(fill, side=side, source=source)
            return
        time.sleep(poll_seconds)


def _submit_limit_exit_order(
    client,
    recorder: Recorder,
    ticker: str,
    quantity: int,
    target_price: int,
    order_limiter: RateLimiter,
    raw_metadata: dict[str, object] | None = None,
) -> None:
    order_limiter.wait()
    sell_order = client.sell_limit(ticker, quantity, target_price)
    existing_raw = sell_order.raw if isinstance(getattr(sell_order, "raw", None), dict) else {}
    sell_order.raw = {**existing_raw, **(raw_metadata or {})}
    recorder.save_order(sell_order)
    if not _record_fill_safely(client, recorder, sell_order.order_id, "SELL", "target_exit"):
        _poll_fill_until_recorded(client, recorder, sell_order.order_id, "SELL", "target_exit_safety_poll")

def submit_exit_order(
    client,
    recorder: Recorder,
    candidate: Candidate,
    fill,
    tick_offset: int,
    order_limiter: RateLimiter,
    stop_loss_tick_count: int,
    stop_loss_tick_multiplier: float,
) -> None:
    decision_fill_price = _resolve_buy_fill_price(fill, candidate.price, candidate.ticker)
    target_price = _safe_target_sell_price(candidate, tick_offset, decision_fill_price)
    stop_loss_metadata = _build_exit_plan_metadata(
        candidate,
        decision_fill_price,
        target_price,
        stop_loss_tick_count,
        stop_loss_tick_multiplier,
    )
    print(
        f"Submitting limit sell for {candidate.ticker}: "
        f"target_price={target_price} stop_loss_price={stop_loss_metadata['planned_stop_loss_price']} "
        f"decision_fill_price={decision_fill_price} raw_fill={fill.raw}"
    )
    _submit_limit_exit_order(
        client,
        recorder,
        candidate.ticker,
        fill.quantity,
        target_price,
        order_limiter,
        raw_metadata=stop_loss_metadata,
    )


def _find_existing_open_sell_price(open_orders: list[dict], ticker: str) -> int:
    ticker_key = _ticker_key(ticker)
    for order in open_orders:
        side_text = str(order.get("io_tp_nm") or order.get("side") or "").strip()
        side_upper = side_text.upper()
        if "SELL" not in side_upper and "매도" not in side_text and "-매도" not in side_text:
            continue
        if _get_open_order_ticker(order) != ticker_key:
            continue
        remaining_quantity = _to_int(
            order.get("oso_qty")
            or order.get("remaining_qty")
            or order.get("rmn_qty")
            or order.get("ord_qty")
        )
        if remaining_quantity <= 0:
            continue
        return _to_int(order.get("ord_pric") or order.get("price") or order.get("unit_cntr_pric"))
    return 0


def _submit_recovery_exit_order_for_buy_delta_fill(
    client,
    recorder: Recorder,
    order: dict[str, object],
    delta_fill: Fill,
    open_orders: list[dict],
    tick_offset: int,
    order_limiter: RateLimiter,
    stop_loss_tick_count: int,
    stop_loss_tick_multiplier: float,
) -> None:
    buy_limit_price = max(0, int(order.get("price") or 0))
    reference_price = buy_limit_price or delta_fill.price
    if reference_price <= 0:
        print(
            f"Skipping recovery exit order for {delta_fill.ticker}: "
            f"invalid reference price order_id={delta_fill.order_id} raw_order={order}"
        )
        return

    target_price = _find_existing_open_sell_price(open_orders, delta_fill.ticker)
    if target_price <= 0:
        synthetic_candidate = Candidate(
            ticker=delta_fill.ticker,
            price=reference_price,
            expect_price=reference_price,
        )
        target_price = _safe_target_sell_price(synthetic_candidate, tick_offset, reference_price)
    tick = get_tick_size(target_price) if target_price > 0 else get_tick_size(reference_price)
    inferred_expect_price = target_price + tick if target_price > 0 and tick > 0 else reference_price
    synthetic_candidate = Candidate(
        ticker=delta_fill.ticker,
        price=reference_price,
        expect_price=inferred_expect_price,
    )
    stop_loss_metadata = _build_exit_plan_metadata(
        synthetic_candidate,
        reference_price,
        target_price,
        stop_loss_tick_count,
        stop_loss_tick_multiplier,
    )
    print(
        f"Recovered additional buy fill for {delta_fill.ticker}: "
        f"delta_quantity={delta_fill.quantity} total_order_fill={delta_fill.raw.get('total_fill_quantity')} "
        f"buy_order_id={delta_fill.order_id} target_price={target_price} "
        f"stop_loss_price={stop_loss_metadata['planned_stop_loss_price']}. Submitting recovery exit order."
    )
    _submit_limit_exit_order(
        client,
        recorder,
        delta_fill.ticker,
        delta_fill.quantity,
        target_price,
        order_limiter,
        raw_metadata=stop_loss_metadata,
    )


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


def _fetch_account_state_safely(client, label: str = "Account state") -> tuple[list, list[dict]] | tuple[None, None]:
    try:
        return client.get_positions(), client.get_open_orders()
    except Exception as exc:
        print(f"{label} fetch failed: {exc}")
        return None, None


def _authenticate_client_safely(client) -> bool:
    try:
        client.auth()
        return True
    except Exception as exc:
        print(f"Client authentication failed: {exc}")
        return False


def _report_risk_recovery_state(client, label: str) -> tuple[list, list[dict]] | tuple[None, None]:
    positions, open_orders = _recheck_account_state(client)
    if positions is None or open_orders is None:
        print(f"{label}: account state could not be confirmed.")
        return None, None
    print(
        f"{label}: pausing new buys and retrying next loop. "
        f"positions={len(positions)} open_orders={len(open_orders)}"
    )
    if has_position(positions):
        print(f"{label}: positions still remain after recovery attempt.")
    if has_open_orders(open_orders):
        print(f"{label}: open orders still remain after recovery attempt.")
    return positions, open_orders


def _attempt_force_sell_safely(client, recorder: Recorder) -> bool:
    try:
        force_sell(client, recorder=recorder)
        return True
    except Exception as exc:
        print(f"Force-sell handling error: {exc}")
        _report_risk_recovery_state(client, "Force-sell recovery")
        try:
            poll_and_record_new_fills(client, recorder)
        except Exception as poll_exc:
            print(f"Force-sell recovery fill poll failed: {poll_exc}")
        return False


def _attempt_stop_loss_safely(
    client,
    recorder: Recorder,
    positions: list,
    open_orders: list[dict],
    cfg: dict,
) -> tuple[bool, bool, str | None]:
    try:
        stop_loss_executed, stop_loss_ticker = monitor_stop_loss(client, recorder, positions, open_orders, cfg)
        return stop_loss_executed, False, stop_loss_ticker
    except Exception as exc:
        print(f"Stop-loss handling error: {exc}")
        _report_risk_recovery_state(client, "Stop-loss recovery")
        try:
            poll_and_record_new_fills(client, recorder, cfg)
        except Exception as poll_exc:
            print(f"Stop-loss recovery fill poll failed: {poll_exc}")
        return False, True, None


def _ticker_key(ticker: str) -> str:
    return str(ticker or "").strip().upper().removeprefix("A")


def filter_candidates_by_prev_scan_jump(
    candidates: list[Candidate],
    previous_scan_prices: dict[str, int],
    max_intraday_jump_from_prev_scan_percent: float = 1.0,
) -> list[Candidate]:
    threshold_percent = float(max_intraday_jump_from_prev_scan_percent or 0.0)
    if threshold_percent <= 0:
        return candidates

    filtered: list[Candidate] = []
    for candidate in candidates:
        previous_price = int(previous_scan_prices.get(_ticker_key(candidate.ticker), 0) or 0)
        if previous_price > 0:
            jump_percent = ((candidate.price - previous_price) / previous_price) * 100
            if jump_percent >= threshold_percent:
                print(
                    f"Skipping {candidate.ticker} due to previous-scan jump: "
                    f"prev_price={previous_price} current_price={candidate.price} "
                    f"jump_percent={jump_percent:.2f} threshold_percent={threshold_percent:.2f}"
                )
                continue
        filtered.append(candidate)
    return filtered


def _get_open_order_ticker(order: dict) -> str:
    return _ticker_key(order.get("ticker") or order.get("stk_cd") or order.get("pdno") or "")


def _get_order_fill(client, order_id: str, ticker: str = "", side: str = ""):
    try:
        return client.get_order_fill(order_id, ticker=ticker, side=side)
    except TypeError:
        return client.get_order_fill(order_id)


def _get_order_fill_with_retry(
    client,
    order_id: str,
    ticker: str = "",
    side: str = "",
    attempts: int = 5,
    sleep_seconds: float = 0.7,
):
    fill = None
    for attempt in range(max(1, attempts)):
        fill = _get_order_fill(client, order_id, ticker=ticker, side=side)
        if fill is not None:
            return fill
        if attempt + 1 < max(1, attempts):
            time.sleep(sleep_seconds)
    return fill


def _normalize_session_date(value: str | None = None) -> str:
    if value:
        return value
    return datetime.now().strftime("%Y-%m-%d")


def reconcile_broker_fills(
    client,
    recorder: Recorder,
    session_date: str | None = None,
) -> dict[str, int]:
    session = _normalize_session_date(session_date)
    if not hasattr(client, "get_grouped_fills"):
        return {
            "broker_fill_count": 0,
            "inserted_or_updated": 0,
            "purged_sell_reconciliation": 0,
        }

    broker_entries = [
        (fill, side)
        for fill, side in client.get_grouped_fills()
        if fill.filled_at.strftime("%Y-%m-%d") == session
    ]
    existing_index = recorder.get_fill_index(session)
    updated = 0

    for fill, side in broker_entries:
        key = (fill.order_id, side)
        existing = existing_index.get(key)
        filled_at_iso = fill.filled_at.isoformat()
        if (
            existing is not None
            and int(existing.get("quantity") or 0) == fill.quantity
            and int(existing.get("price") or 0) == fill.price
            and str(existing.get("filled_at") or "") == filled_at_iso
            and str(existing.get("source") or "") == "eod_reconciliation"
        ):
            continue
        recorder.replace_fill(fill, side=side, source="eod_reconciliation")
        updated += 1

    purged = 0
    if hasattr(recorder, "purge_superseded_sell_reconciliation_fills"):
        purged = int(recorder.purge_superseded_sell_reconciliation_fills(session) or 0)
    recorder.rebuild_session_fill_exports(session)
    return {
        "broker_fill_count": len(broker_entries),
        "inserted_or_updated": updated,
        "purged_sell_reconciliation": purged,
    }


def _get_position_quantity_by_ticker(positions: list) -> dict[str, int]:
    quantities: dict[str, int] = {}
    for position in positions:
        ticker = _ticker_key(getattr(position, "ticker", ""))
        quantity = max(0, int(getattr(position, "quantity", 0) or 0))
        if not ticker:
            continue
        quantities[ticker] = quantities.get(ticker, 0) + quantity
    return quantities


def _get_open_orders_by_id(open_orders: list[dict]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for order in open_orders:
        order_id = str(order.get("order_id") or order.get("ord_no") or order.get("id") or "").strip()
        if order_id:
            indexed[order_id] = order
    return indexed


def _infer_sell_fill_quantity(
    order: dict[str, object],
    position_quantities: dict[str, int],
    open_orders_by_id: dict[str, dict],
) -> int:
    total_quantity = max(0, int(order.get("quantity") or 0))
    if total_quantity <= 0:
        return 0

    open_order = open_orders_by_id.get(str(order.get("broker_order_id") or "").strip())
    if open_order is not None:
        remaining_open_quantity = _to_int(
            open_order.get("oso_qty")
            or open_order.get("remaining_qty")
            or open_order.get("rmn_qty")
            or open_order.get("ord_qty")
        )
        return max(0, min(total_quantity, total_quantity - remaining_open_quantity))

    ticker = _ticker_key(str(order.get("ticker") or ""))
    remaining_position_quantity = min(total_quantity, position_quantities.get(ticker, 0))
    return max(0, total_quantity - remaining_position_quantity)


def _has_superseding_sell_fill(
    recorder: Recorder,
    order: dict[str, object],
    minimum_quantity: int,
) -> bool:
    if minimum_quantity <= 0 or not hasattr(recorder, "has_recorded_sell_fill_after"):
        return False
    try:
        return bool(
            recorder.has_recorded_sell_fill_after(
                ticker=str(order.get("ticker") or ""),
                created_at=str(order.get("created_at") or ""),
                exclude_order_id=str(order.get("broker_order_id") or ""),
                minimum_quantity=minimum_quantity,
            )
        )
    except Exception as exc:
        print(
            "Failed to check for superseding sell fill "
            f"for order_id={order.get('broker_order_id')} ticker={order.get('ticker')}: {exc}"
        )
        return False


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


def resolve_session_capital_basis(client) -> int:
    try:
        orderable_cash = client.get_orderable_cash()
    except Exception as exc:
        print(f"Failed to fetch orderable cash for session slot plan: {exc}")
        orderable_cash = 0
    if orderable_cash > 0:
        return orderable_cash

    fallback_value = estimate_account_value(client)
    print(
        "Falling back to account value estimate for session slot plan: "
        f"orderable_cash={orderable_cash} fallback_value={fallback_value}"
    )
    return fallback_value


def is_daily_loss_limit_reached(
    client,
    cfg: dict,
    initial_account_value: int,
    session_started_at: datetime | None = None,
    positions: list | None = None,
    open_orders: list[dict] | None = None,
    recorder: Recorder | None = None,
    kospi_change_percent: float | None = None,
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
            kospi_change_percent=kospi_change_percent,
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


def poll_and_record_new_fills(client, recorder: Recorder, cfg: dict | None = None) -> None:
    if not hasattr(client, "get_order_fill"):
        return
    order_limiter = None
    tick_offset = 1
    if cfg is not None:
        order_limiter = RateLimiter(cfg["api"]["order_rate_limit_per_second"])
        tick_offset = int(cfg["strategy"].get("sell_tick_offset", 1) or 1)
    stop_loss_tick_count = _resolve_stop_loss_tick_count(cfg)
    stop_loss_tick_multiplier = _resolve_stop_loss_tick_multiplier(cfg)
    try:
        positions = client.get_positions()
    except Exception as exc:
        print(f"Failed to fetch positions for fill reconciliation: {exc}")
        positions = []
    try:
        open_orders = client.get_open_orders()
    except Exception as exc:
        print(f"Failed to fetch open orders for fill reconciliation: {exc}")
        open_orders = []
    position_quantities = _get_position_quantity_by_ticker(positions)
    open_orders_by_id = _get_open_orders_by_id(open_orders)
    for order in recorder.get_orders_needing_fill_poll():
        order_id = str(order.get("broker_order_id") or "").strip()
        ticker = str(order.get("ticker") or "").strip()
        side = str(order.get("side") or "").strip().upper()
        already_recorded = int(order.get("recorded_fill_quantity") or 0)
        if not order_id or side not in {"BUY", "SELL"}:
            continue
        try:
            fill = _get_order_fill(client, order_id, ticker=ticker, side=side)
        except Exception as exc:
            print(f"Failed to poll fill for order_id={order_id} ticker={order.get('ticker')}: {exc}")
            continue
        if fill is None or fill.quantity <= already_recorded:
            if side == "SELL":
                try:
                    fill = _get_order_fill_with_retry(
                        client,
                        order_id,
                        ticker=ticker,
                        side=side,
                        attempts=4,
                        sleep_seconds=0.7,
                    )
                except Exception as exc:
                    print(f"Retry fill lookup failed for order_id={order_id} ticker={ticker}: {exc}")
                    fill = None
            if fill is not None and fill.quantity > already_recorded:
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
                if side == "BUY" and order_limiter is not None:
                    try:
                        _submit_recovery_exit_order_for_buy_delta_fill(
                            client,
                            recorder,
                            order,
                            delta_fill,
                            open_orders,
                            tick_offset,
                            order_limiter,
                            stop_loss_tick_count,
                            stop_loss_tick_multiplier,
                        )
                    except Exception as exc:
                        print(
                            f"Failed to submit recovery exit order for "
                            f"buy delta fill order_id={order_id} ticker={ticker}: {exc}"
                        )
                continue
            if side != "SELL":
                continue
            inferred_total_quantity = _infer_sell_fill_quantity(order, position_quantities, open_orders_by_id)
            if inferred_total_quantity <= already_recorded:
                continue
            delta_quantity = inferred_total_quantity - already_recorded
            if _has_superseding_sell_fill(recorder, order, delta_quantity):
                continue
            delta_fill = Fill(
                order_id=order_id,
                ticker=str(order.get("ticker") or ""),
                quantity=delta_quantity,
                price=int(order.get("price") or 0),
                raw={"source": "sell_reconciliation", "reason": "fill_lookup_missing"},
            )
            recorder.save_fill(delta_fill, side=side, source="sell_reconciliation")
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
        if side == "BUY" and order_limiter is not None:
            try:
                _submit_recovery_exit_order_for_buy_delta_fill(
                    client,
                    recorder,
                    order,
                    delta_fill,
                    open_orders,
                    tick_offset,
                    order_limiter,
                    stop_loss_tick_count,
                    stop_loss_tick_multiplier,
                )
            except Exception as exc:
                print(
                    f"Failed to submit recovery exit order for "
                    f"buy delta fill order_id={order_id} ticker={ticker}: {exc}"
                )


def submit_target_exit_order_from_position_if_present(
    client,
    recorder: Recorder,
    candidate: Candidate,
    buy_order_id: str,
    tick_offset: int,
    order_limiter: RateLimiter,
    stop_loss_tick_count: int,
    stop_loss_tick_multiplier: float,
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
    stop_loss_metadata = _build_exit_plan_metadata(
        candidate,
        buy_reference_price,
        target_price,
        stop_loss_tick_count,
        stop_loss_tick_multiplier,
    )
    print(
        f"Position recovered for {candidate.ticker} after fill lookup failed: "
        f"quantity={quantity} target_price={target_price} "
        f"stop_loss_price={stop_loss_metadata['planned_stop_loss_price']}. Submitting target limit sell."
    )
    order_limiter.wait()
    sell_order = client.sell_limit(candidate.ticker, quantity, target_price)
    existing_raw = sell_order.raw if isinstance(getattr(sell_order, "raw", None), dict) else {}
    sell_order.raw = {**existing_raw, **stop_loss_metadata}
    recorder.save_order(sell_order)
    if not _record_fill_safely(client, recorder, sell_order.order_id, "SELL", "target_exit_recovery"):
        _poll_fill_until_recorded(client, recorder, sell_order.order_id, "SELL", "target_exit_recovery_safety_poll")
    return True


def activate_buy(
    client,
    recorder: Recorder,
    targets: list[Candidate],
    cfg: dict,
    slot_budget_per_stock: int | None = None,
    position_limit: int | None = None,
) -> None:
    order_limiter = RateLimiter(cfg["api"]["order_rate_limit_per_second"])
    budget_per_cycle = cfg["risk"].get("max_budget_per_cycle_krw", 0)
    tick_offset = cfg["strategy"]["sell_tick_offset"]
    stop_loss_tick_count = _resolve_stop_loss_tick_count(cfg)
    stop_loss_tick_multiplier = _resolve_stop_loss_tick_multiplier(cfg)
    try:
        orderable_cash = client.get_orderable_cash()
    except Exception as exc:
        print(f"Failed to fetch orderable cash: {exc}")
        return
    if orderable_cash <= 0:
        print("Orderable cash is zero. Skip buy cycle.")
        return
    remaining_cash = min(orderable_cash, budget_per_cycle) if budget_per_cycle > 0 else orderable_cash
    fixed_budget_per_stock = int(slot_budget_per_stock) if slot_budget_per_stock is not None else None
    effective_position_limit = (
        int(position_limit)
        if position_limit is not None
        else int(cfg["risk"].get("max_position_count", cfg["strategy"].get("max_buy_count", 0)) or 0)
    )

    for index, candidate in enumerate(targets):
        try:
            if effective_position_limit > 0:
                positions, open_orders = _recheck_account_state(client)
                if positions is None or open_orders is None:
                    print("Stopping new buys because current position limit could not be confirmed.")
                    return
                active_tickers = _get_active_tickers(positions, open_orders)
                if len(active_tickers) >= effective_position_limit:
                    print(
                        f"Max position count reached during buy loop: "
                        f"active={len(active_tickers)} limit={effective_position_limit}. Stopping new buys."
                    )
                    break

            if fixed_budget_per_stock is not None:
                per_stock_budget = min(fixed_budget_per_stock, remaining_cash) if fixed_budget_per_stock > 0 else remaining_cash
            else:
                budget_per_stock = cfg["risk"]["max_budget_per_stock_krw"]
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
                    submit_exit_order(
                        client,
                        recorder,
                        candidate,
                        partial_fill,
                        tick_offset,
                        order_limiter,
                        stop_loss_tick_count,
                        stop_loss_tick_multiplier,
                    )
                    return
                if submit_target_exit_order_from_position_if_present(
                    client,
                    recorder,
                    candidate,
                    buy_order.order_id,
                    tick_offset,
                    order_limiter,
                    stop_loss_tick_count,
                    stop_loss_tick_multiplier,
                ):
                    return
                refreshed_cash = _refresh_remaining_cash(client, budget_per_cycle)
                if refreshed_cash is None:
                    return
                remaining_cash = refreshed_cash
                continue

            recorder.save_fill(fill, side="BUY", source="wait_buy_filled")
            submit_exit_order(
                client,
                recorder,
                candidate,
                fill,
                tick_offset,
                order_limiter,
                stop_loss_tick_count,
                stop_loss_tick_multiplier,
            )
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
    recorder = Recorder(BOT_DB_PATH, log_dir=BOT_LOG_DIR)
    while not _authenticate_client_safely(client):
        time.sleep(5)
    state = BotState.NO_POSITION
    force_sell_done = False
    startup_carryover_clear_done = False
    eod_reconciliation_done = False
    daily_revenue_written = False
    warmed_session = False
    blocked_stop_loss_tickers: set[str] = set()
    watchlist: dict[str, Candidate] = {}
    previous_scan_prices: dict[str, int] = {}
    prev_close_prices: dict[str, int] = {}
    session_started_at = datetime.now()
    initial_account_value = estimate_account_value(client)
    session_capital_basis = 0
    session_slot_count = 0
    session_slot_budget_per_stock = 0
    session_position_limit = 0
    print(f"Initial account value estimate: {initial_account_value}")
    active_poll_seconds = min(5, max(1, int(cfg["strategy"].get("scan_interval_seconds", 60))))
    kospi_change_percent = resolve_kospi_change_percent()

    while True:
        in_buy_window = is_between_now(cfg["market"]["start_buy_time"], cfg["market"]["stop_buy_time"])
        buy_window_started = is_after_now(cfg["market"]["start_buy_time"])

        if force_sell_done and not eod_reconciliation_done and is_after_now(cfg["market"].get("reconcile_time", "15:15")):
            try:
                summary = reconcile_broker_fills(client, recorder)
                recorder.write_daily_revenue_summary(datetime.now().strftime("%Y-%m-%d"), initial_account_value)
                daily_revenue_written = True
                eod_reconciliation_done = True
                print(
                    "End-of-day reconciliation completed: "
                    f"broker_fills={summary['broker_fill_count']} updated={summary['inserted_or_updated']}"
                )
            except Exception as exc:
                print(f"End-of-day reconciliation failed: {exc}")
            if is_after_now(cfg["market"].get("end_time", "15:20")) or eod_reconciliation_done:
                break

        if is_after_now(cfg["market"]["force_sell_time"]) and not force_sell_done:
            state = BotState.FORCE_SELLING
            if _attempt_force_sell_safely(client, recorder):
                poll_and_record_new_fills(client, recorder, cfg)
                force_sell_done = True
                state = BotState.STOPPED
                print("Force sell completed. Waiting for end-of-day reconciliation.")
                time.sleep(5)
            else:
                print("Force sell did not complete cleanly. New buys remain blocked and the next loop will retry.")
                time.sleep(5)
            continue

        if force_sell_done:
            if is_after_now(cfg["market"].get("end_time", "15:20")):
                if not daily_revenue_written:
                    recorder.write_daily_revenue_summary(datetime.now().strftime("%Y-%m-%d"), initial_account_value)
                    daily_revenue_written = True
                break
            time.sleep(5)
            continue

        if is_between_now(cfg["market"].get("prewarm_start_time", cfg["market"]["start_buy_time"]), cfg["market"]["start_buy_time"]) and not warmed_session:
            try:
                warm_universe(cfg)
                prev_close_prices = record_session_prev_close_prices(recorder, cfg)
                session_capital_basis = resolve_session_capital_basis(client)
                session_slot_count = resolve_total_slot_count(cfg, session_capital_basis)
                session_slot_budget_per_stock = resolve_target_budget_per_stock(cfg, session_capital_basis)
                session_position_limit = resolve_position_limit(cfg, session_slot_count)
                warmed_session = True
                print(
                    "Universe warm-up completed. "
                    f"prev_close_count={len(prev_close_prices)} "
                    f"session_capital_basis={session_capital_basis} "
                    f"session_slot_count={session_slot_count} "
                    f"slot_budget_per_stock={session_slot_budget_per_stock} "
                    f"position_limit={session_position_limit}"
                )
            except Exception as exc:
                print(f"Universe warm-up failed: {exc}")

        if not startup_carryover_clear_done:
            if not is_after_now(cfg["market"].get("startup_clear_time", "09:10")):
                time.sleep(5)
                continue
            state = BotState.FORCE_SELLING
            startup_carryover_cleared, startup_carryover_error = _attempt_startup_carryover_liquidation_safely(client, recorder)
            if startup_carryover_cleared:
                startup_carryover_clear_done = True
                state = BotState.NO_POSITION
                print("Startup carryover liquidation completed. Proceeding with normal session flow.")
            elif startup_carryover_error:
                time.sleep(5)
            continue

        if not buy_window_started:
            time.sleep(5)
            continue

        if not warmed_session:
            prev_close_prices = record_session_prev_close_prices(recorder, cfg)
            session_capital_basis = resolve_session_capital_basis(client)
            session_slot_count = resolve_total_slot_count(cfg, session_capital_basis)
            session_slot_budget_per_stock = resolve_target_budget_per_stock(cfg, session_capital_basis)
            session_position_limit = resolve_position_limit(cfg, session_slot_count)
            warmed_session = True
            print(
                "Session slot plan initialized without prewarm. "
                f"prev_close_count={len(prev_close_prices)} "
                f"session_capital_basis={session_capital_basis} "
                f"session_slot_count={session_slot_count} "
                f"slot_budget_per_stock={session_slot_budget_per_stock} "
                f"position_limit={session_position_limit}"
            )

        positions, open_orders = _fetch_account_state_safely(client, "Main loop account state")
        if positions is None or open_orders is None:
            time.sleep(5)
            continue
        active_tickers = _get_active_tickers(positions, open_orders)
        poll_and_record_new_fills(client, recorder, cfg)
        positions, open_orders = _fetch_account_state_safely(client, "Post-fill account refresh")
        if positions is None or open_orders is None:
            time.sleep(5)
            continue
        active_tickers = _get_active_tickers(positions, open_orders)
        if watchlist:
            watchlist = trace_candidate_watchlist(
                client=client,
                recorder=recorder,
                candidates=watchlist,
                quote_rate_limit_per_second=cfg["api"]["quote_rate_limit_per_second"],
                sell_tick_offset=cfg["strategy"]["sell_tick_offset"],
                selected_keys=active_tickers,
                kospi_change_percent=kospi_change_percent,
            )
        if has_position(positions):
            trace_active_positions(
                client=client,
                recorder=recorder,
                positions=positions,
                quote_rate_limit_per_second=cfg["api"]["quote_rate_limit_per_second"],
                kospi_change_percent=kospi_change_percent,
            )
        if has_position(positions):
            stop_loss_executed, stop_loss_error, stop_loss_ticker = _attempt_stop_loss_safely(
                client,
                recorder,
                positions,
                open_orders,
                cfg,
            )
            poll_and_record_new_fills(client, recorder, cfg)
            if stop_loss_executed:
                if stop_loss_ticker:
                    blocked_ticker_key = _ticker_key(stop_loss_ticker)
                    blocked_stop_loss_tickers.add(blocked_ticker_key)
                    print(
                        f"Blocking same-day re-entry after stop-loss for {stop_loss_ticker}. "
                        f"blocked_tickers={sorted(blocked_stop_loss_tickers)}"
                    )
                time.sleep(1)
                continue
            if stop_loss_error:
                time.sleep(5)
                continue

        if is_daily_loss_limit_reached(
            client,
            cfg,
            initial_account_value,
            session_started_at,
            positions,
            open_orders,
            recorder=recorder,
            kospi_change_percent=kospi_change_percent,
        ):
            time.sleep(5)
            continue

        if not in_buy_window:
            time.sleep(active_poll_seconds if active_tickers else 5)
            continue

        if session_position_limit > 0 and len(active_tickers) >= session_position_limit:
            time.sleep(active_poll_seconds if active_tickers else 5)
            continue

        if should_wait_for_full_batch_exit(
            len(active_tickers),
            allow_refill_empty_slots=bool(cfg["strategy"].get("allow_refill_empty_slots", True)),
        ):
            time.sleep(active_poll_seconds if active_tickers else 5)
            continue

        state = BotState.SCANNING
        try:
            kospi_change_percent = resolve_kospi_change_percent()
            calculated = scan_and_rank(
                client,
                recorder,
                cfg,
                kospi_change_percent=kospi_change_percent,
                prev_close_prices=prev_close_prices,
            )
        except Exception as exc:
            print(f"Scan cycle failed: {exc}")
            time.sleep(cfg["strategy"]["scan_interval_seconds"])
            continue
        positions, open_orders = _fetch_account_state_safely(client, "Post-scan account refresh")
        if positions is None or open_orders is None:
            time.sleep(active_poll_seconds if active_tickers else cfg["strategy"]["scan_interval_seconds"])
            continue
        active_tickers = _get_active_tickers(positions, open_orders)
        filtered, used_expected_return_threshold = filter_candidates_for_entry(
            calculated,
            cfg,
            previous_scan_prices=previous_scan_prices,
            active_tickers=active_tickers,
            blocked_tickers=blocked_stop_loss_tickers,
            allow_refill_empty_slots=bool(cfg["strategy"].get("allow_refill_empty_slots", True)),
        )
        previous_scan_prices = {
            _ticker_key(candidate.ticker): int(candidate.price)
            for candidate in calculated
            if int(candidate.price or 0) > 0
        }
        if filtered and used_expected_return_threshold != float(cfg["strategy"]["min_expected_return_percent"]):
            print(
                "Using fallback expected-return threshold after prev-scan jump filter: "
                f"{used_expected_return_threshold:.2f} candidates={len(filtered)}"
            )
        for candidate in filtered:
            watchlist[_ticker_key(candidate.ticker)] = candidate
        empty_slots = resolve_empty_slots(session_position_limit, len(active_tickers), len(filtered))
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
        buy_count = resolve_buy_count(
            cfg,
            empty_slots,
            planning_cash,
            target_budget_per_stock=session_slot_budget_per_stock,
        )
        if buy_count <= 0:
            time.sleep(active_poll_seconds if active_tickers else cfg["strategy"]["scan_interval_seconds"])
            continue
        targets = select_affordable_targets(
            filtered,
            buy_count,
            planning_cash,
            session_slot_budget_per_stock,
            cfg["strategy"]["sell_tick_offset"],
            cfg["risk"].get("max_orderbook_ask_depth_ratio", 0.20),
        )
        if targets and len(targets) < buy_count:
            print(f"Planned {len(targets)} affordable targets out of desired {buy_count} due to cash constraints.")
        for target in targets:
            recorder.save_signal(target, selected=True)
        if targets:
            state = BotState.BUYING
            activate_buy(
                client,
                recorder,
                targets,
                cfg,
                slot_budget_per_stock=session_slot_budget_per_stock,
                position_limit=session_position_limit,
            )
            state = BotState.SELLING
        time.sleep(active_poll_seconds if active_tickers else cfg["strategy"]["scan_interval_seconds"])


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
