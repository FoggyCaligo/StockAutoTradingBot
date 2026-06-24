from __future__ import annotations

import time
from typing import Any

from Daily_bot.models import HogaSnapshot, Position
from Daily_bot.storage.db import Recorder


def _get_order_id(order: dict) -> str:
    return str(order.get("order_id") or order.get("ord_no") or order.get("id") or "").strip()


def _get_order_id_from_object(order: object) -> str:
    return str(
        getattr(order, "order_id", None)
        or getattr(order, "ord_no", None)
        or getattr(order, "id", None)
        or ""
    ).strip()


def _get_ticker(order: dict) -> str:
    return str(order.get("ticker") or order.get("stk_cd") or order.get("pdno") or "").strip().upper().removeprefix("A")


def _get_remaining_quantity(order: dict) -> int:
    value = order.get("oso_qty") or order.get("remaining_qty") or order.get("rmn_qty") or order.get("ord_qty") or 0
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError, AttributeError):
        return 0.0


def get_position_loss_percent(position: Position) -> float | None:
    raw_attr = getattr(position, "raw", None)
    raw = raw_attr if isinstance(raw_attr, dict) else {}
    if "prft_rt" in raw:
        return _to_float(raw.get("prft_rt"))
    if position.avg_price <= 0:
        return None
    current_price = _to_float(raw.get("cur_prc"))
    if current_price <= 0:
        return None
    return ((current_price - position.avg_price) / position.avg_price) * 100


def is_stop_loss_triggered(position: Position, current_price: int, stop_loss_percent: float) -> bool:
    if position.avg_price <= 0 or current_price <= 0 or stop_loss_percent <= 0:
        return False
    threshold_price = position.avg_price * (1 - stop_loss_percent / 100)
    return current_price <= threshold_price


def is_stop_loss_triggered_by_price(current_price: int, stop_loss_price: int) -> bool:
    if current_price <= 0 or stop_loss_price <= 0:
        return False
    return current_price <= stop_loss_price


def get_stop_loss_reference_price(snapshot: HogaSnapshot) -> int:
    """Use a conservative executable price for stop-loss checks.

    A market sell is much closer to the best bid than to the latest last-trade
    price, so prefer the top bid when available.
    """
    best_bid = snapshot.bids[0].price if snapshot.bids else 0
    if best_bid > 0:
        return best_bid
    return int(snapshot.current_price or 0)


def get_stop_loss_limit_price(snapshot: HogaSnapshot) -> int:
    """Place stop-loss exits as executable sell limits near the top bid."""
    return get_stop_loss_reference_price(snapshot)


def get_planned_stop_loss_price(recorder: Recorder | Any, ticker: str) -> int:
    if recorder is None or not hasattr(recorder, "get_latest_planned_stop_loss_price"):
        return 0
    try:
        return int(recorder.get_latest_planned_stop_loss_price(ticker) or 0)
    except Exception as exc:
        print(f"Failed to load planned stop-loss price for {ticker}: {exc}")
        return 0


def is_stop_loss_enabled(cfg: dict) -> bool:
    risk_cfg = cfg.get("risk", {}) if isinstance(cfg, dict) else {}
    stop_loss_percent = float(risk_cfg.get("stop_loss_percent", 0.0) or 0.0)
    stop_loss_tick_count = int(risk_cfg.get("stop_loss_tick_count", 0) or 0)
    stop_loss_tick_multiplier = float(risk_cfg.get("stop_loss_tick_multiplier", 0.0) or 0.0)
    return stop_loss_percent > 0 or stop_loss_tick_count > 0 or stop_loss_tick_multiplier > 0


def wait_until_no_open_orders_for_ticker(
    client,
    ticker: str,
    timeout_seconds: int = 30,
    poll_seconds: float = 1.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        open_orders = client.get_open_orders()
        if not any(_get_ticker(order) == ticker for order in open_orders):
            return True
        time.sleep(poll_seconds)
    return not any(_get_ticker(order) == ticker for order in client.get_open_orders())


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


def monitor_stop_loss(client, recorder: Recorder, positions: list[Position], open_orders: list[dict], cfg: dict) -> bool:
    if not is_stop_loss_enabled(cfg):
        return False

    stop_loss_percent = float(cfg["risk"].get("stop_loss_percent", 2.0))

    for position in positions:
        snapshot = client.get_20hoga(position.ticker)
        limit_price = get_stop_loss_limit_price(snapshot)
        reference_price = get_stop_loss_reference_price(snapshot)
        planned_stop_loss_price = get_planned_stop_loss_price(recorder, position.ticker)

        if planned_stop_loss_price > 0 and is_stop_loss_triggered_by_price(reference_price, planned_stop_loss_price):
            print(
                "Stop-loss triggered from planned stop-loss price: "
                f"{position.ticker} avg={position.avg_price} ref_price={reference_price} "
                f"planned_stop_loss_price={planned_stop_loss_price} limit_price={limit_price}"
            )
        else:
            loss_percent = get_position_loss_percent(position)
            if loss_percent is not None and stop_loss_percent > 0 and loss_percent <= -stop_loss_percent:
                print(
                    "Stop-loss triggered from account snapshot fallback: "
                    f"{position.ticker} avg={position.avg_price} loss_percent={loss_percent:.2f} limit_price={limit_price}"
                )
            else:
                if not is_stop_loss_triggered(position, reference_price, stop_loss_percent):
                    continue

                threshold_price = position.avg_price * (1 - stop_loss_percent / 100)
                print(
                    "Stop-loss triggered from hoga snapshot fallback: "
                    f"{position.ticker} avg={position.avg_price} "
                    f"ref_price={reference_price} threshold={int(threshold_price)} limit_price={limit_price}"
                )

        for order in open_orders:
            if _get_ticker(order) != position.ticker:
                continue
            order_id = _get_order_id(order)
            quantity = _get_remaining_quantity(order)
            if order_id:
                client.cancel_order(order_id, ticker=position.ticker, quantity=quantity)

        cancelled = wait_until_no_open_orders_for_ticker(client, position.ticker)
        if not cancelled:
            raise RuntimeError(
                f"Stop-loss triggered for {position.ticker}, but existing orders could not be cancelled safely."
            )

        if limit_price <= 0:
            raise RuntimeError(f"Stop-loss triggered for {position.ticker}, but no valid executable limit price was available.")

        sell_order = client.sell_limit(position.ticker, position.quantity, limit_price)
        recorder.save_order(sell_order)
        sell_order_id = _get_order_id_from_object(sell_order)
        if not _record_fill_safely(client, recorder, sell_order_id, "SELL", "stop_loss"):
            _poll_fill_until_recorded(client, recorder, sell_order_id, "SELL", "stop_loss_safety_poll")
        return True

    return False
