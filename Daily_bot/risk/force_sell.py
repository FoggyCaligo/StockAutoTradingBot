from __future__ import annotations

import time

from Daily_bot.storage.db import Recorder


def _to_int(value, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _get_order_id(order: dict) -> str:
    return str(order.get("order_id") or order.get("ord_no") or order.get("id") or "").strip()


def _get_ticker(order: dict) -> str:
    return str(order.get("ticker") or order.get("stk_cd") or order.get("pdno") or "").strip()


def _get_remaining_quantity(order: dict) -> int:
    return _to_int(
        order.get("oso_qty")
        or order.get("remaining_qty")
        or order.get("rmn_qty")
        or order.get("ord_qty"),
        default=0,
    )


def cancel_all_open_orders(client) -> None:
    for order in client.get_open_orders():
        order_id = _get_order_id(order)
        ticker = _get_ticker(order)
        quantity = _get_remaining_quantity(order)
        if order_id:
            client.cancel_order(order_id, ticker=ticker, quantity=quantity)


def wait_until_all_orders_cancelled(client, timeout_seconds: int = 60, poll_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not client.get_open_orders():
            return True
        time.sleep(poll_seconds)
    return not client.get_open_orders()


def sell_all_positions_market(client, recorder: Recorder | None = None) -> None:
    for position in client.get_positions():
        if position.quantity > 0:
            sell_order = client.sell_market(position.ticker, position.quantity)
            if recorder is not None:
                recorder.save_order(sell_order)


def force_sell(client, recorder: Recorder | None = None) -> None:
    """Cancel all open orders, confirm cancellation, then sell positions at market."""
    cancel_all_open_orders(client)
    cancelled = wait_until_all_orders_cancelled(client)
    if not cancelled:
        raise RuntimeError("Open orders still remain after cancellation timeout. Refusing market sell to avoid order conflict.")
    sell_all_positions_market(client, recorder=recorder)
    if hasattr(client, "wait_until_no_position"):
        client.wait_until_no_position()
