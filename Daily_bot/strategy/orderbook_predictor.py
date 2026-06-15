from __future__ import annotations

from copy import deepcopy

from Daily_bot.models import HogaLevel, HogaSnapshot
from Daily_bot.utils import get_tick_size, round_to_tick


def predict_price_from_hoga(snapshot: HogaSnapshot) -> int:
    """Predict price by offsetting bid/ask volumes 1:1 across hoga levels.

    Assumptions:
    - bids sorted from highest bid to lower prices.
    - asks sorted from lowest ask to higher prices.
    - When either side reaches the end of the 20-level orderbook, stop.
    - Return the midpoint between current bid and ask frontier.

    This is a clean-room skeleton implementation of the user's original
    orderbook offset idea. Validate against the old predict_priceidx before use.
    """
    bids: list[HogaLevel] = deepcopy(snapshot.bids)
    asks: list[HogaLevel] = deepcopy(snapshot.asks)

    if not bids or not asks:
        return snapshot.current_price

    bid_idx = 0
    ask_idx = 0

    while bid_idx < len(bids) and ask_idx < len(asks):
        offset = min(bids[bid_idx].volume, asks[ask_idx].volume)
        bids[bid_idx].volume -= offset
        asks[ask_idx].volume -= offset

        if bids[bid_idx].volume <= 0:
            bid_idx += 1
        if asks[ask_idx].volume <= 0:
            ask_idx += 1

    frontier_bid_price = bids[min(bid_idx, len(bids) - 1)].price
    frontier_ask_price = asks[min(ask_idx, len(asks) - 1)].price
    predicted = (frontier_bid_price + frontier_ask_price) // 2
    return round_to_tick(predicted)


def calc_spread_percent(snapshot: HogaSnapshot) -> float:
    if not snapshot.bids or not snapshot.asks or snapshot.current_price <= 0:
        return 999.0
    best_bid = snapshot.bids[0].price
    best_ask = snapshot.asks[0].price
    return (best_ask - best_bid) / snapshot.current_price * 100


def calc_ask_depth_amount(snapshot: HogaSnapshot, levels: int = 5) -> int:
    if levels <= 0 or not snapshot.asks:
        return 0
    return sum(max(0, level.price) * max(0, level.volume) for level in snapshot.asks[:levels])


def calc_target_sell_price(expect_price: int, tick_offset: int = 1) -> int:
    tick = get_tick_size(expect_price)
    return round_to_tick(expect_price - tick * tick_offset)
