from __future__ import annotations


def cancel_all_open_orders(client) -> None:
    for order in client.get_open_orders():
        order_id = order.get("order_id") or order.get("ord_no") or order.get("id")
        if order_id:
            client.cancel_order(order_id)


def sell_all_positions_market(client) -> None:
    for position in client.get_positions():
        if position.quantity > 0:
            client.sell_market(position.ticker, position.quantity)


def force_sell(client) -> None:
    """Cancel all open orders and sell all positions at market."""
    cancel_all_open_orders(client)
    sell_all_positions_market(client)
