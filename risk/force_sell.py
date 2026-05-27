from __future__ import annotations


def _to_int(value, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def cancel_all_open_orders(client) -> None:
    for order in client.get_open_orders():
        order_id = order.get("order_id") or order.get("ord_no") or order.get("id")
        ticker = order.get("ticker") or order.get("stk_cd") or order.get("pdno") or ""
        quantity = _to_int(order.get("oso_qty") or order.get("remaining_qty") or order.get("ord_qty"), default=0)
        if order_id:
            client.cancel_order(order_id, ticker=ticker, quantity=quantity)


def sell_all_positions_market(client) -> None:
    for position in client.get_positions():
        if position.quantity > 0:
            client.sell_market(position.ticker, position.quantity)


def force_sell(client) -> None:
    """Cancel all open orders and sell all positions at market."""
    cancel_all_open_orders(client)
    sell_all_positions_market(client)
