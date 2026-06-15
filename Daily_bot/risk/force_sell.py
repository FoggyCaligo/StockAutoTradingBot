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


def sell_all_positions_at_current_price(client, recorder: Recorder | None = None) -> None:
    for position in client.get_positions():
        if position.quantity <= 0:
            continue

        print(
            f"Submitting market force sell for {position.ticker}: "
            f"quantity={position.quantity}"
        )
        sell_order = client.sell_market(position.ticker, position.quantity)

        if recorder is not None:
            recorder.save_order(sell_order)
            sell_order_id = _get_order_id_from_object(sell_order)
            if not _record_fill_safely(client, recorder, sell_order_id, "SELL", "force_sell"):
                _poll_fill_until_recorded(client, recorder, sell_order_id, "SELL", "force_sell_safety_poll")


def force_sell(client, recorder: Recorder | None = None) -> None:
    """Cancel all open orders, confirm cancellation, then force-sell positions at market."""
    cancel_all_open_orders(client)
    cancelled = wait_until_all_orders_cancelled(client)
    if not cancelled:
        raise RuntimeError("Open orders still remain after cancellation timeout. Refusing force sell to avoid order conflict.")
    sell_all_positions_at_current_price(client, recorder=recorder)
    if hasattr(client, "wait_until_no_position"):
        client.wait_until_no_position()
