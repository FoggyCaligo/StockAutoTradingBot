from __future__ import annotations

import time

from Daily_bot.models import Position
from Daily_bot.storage.db import Recorder


def _get_order_id(order: dict) -> str:
    return str(order.get("order_id") or order.get("ord_no") or order.get("id") or "").strip()


def _get_ticker(order: dict) -> str:
    return str(order.get("ticker") or order.get("stk_cd") or order.get("pdno") or "").strip()


def _get_remaining_quantity(order: dict) -> int:
    value = order.get("oso_qty") or order.get("remaining_qty") or order.get("rmn_qty") or order.get("ord_qty") or 0
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def is_stop_loss_triggered(position: Position, current_price: int, stop_loss_percent: float) -> bool:
    if position.avg_price <= 0 or current_price <= 0 or stop_loss_percent <= 0:
        return False
    threshold_price = position.avg_price * (1 - stop_loss_percent / 100)
    return current_price <= threshold_price


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


def _record_fill_safely(client, recorder: Recorder, order_id: str, side: str, source: str) -> None:
    if not order_id or not hasattr(client, "get_order_fill"):
        return
    try:
        fill = client.get_order_fill(order_id)
        if fill:
            recorder.save_fill(fill, side=side, source=source)
    except Exception as exc:
        print(f"Warning: Failed to record immediate fill for {side} order {order_id} ({source}): {exc}")


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
    stop_loss_percent = float(cfg["risk"].get("stop_loss_percent", 2.0))
    if stop_loss_percent <= 0:
        return False

    for position in positions:
        snapshot = client.get_20hoga(position.ticker)
        if not is_stop_loss_triggered(position, snapshot.current_price, stop_loss_percent):
            continue

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

        sell_order = client.sell_market(position.ticker, position.quantity)
        recorder.save_order(sell_order)
        sell_order_id = _get_order_id(sell_order.__dict__)
        _record_fill_safely(client, recorder, sell_order_id, "SELL", "stop_loss")
        _poll_fill_until_recorded(client, recorder, sell_order_id, "SELL", "stop_loss_safety_poll")
        return True

    return False
