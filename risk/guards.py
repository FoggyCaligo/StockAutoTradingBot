from __future__ import annotations

from datetime import datetime

from models import Candidate, Position
from strategy.orderbook_predictor import calc_target_sell_price


def has_position(positions: list[Position]) -> bool:
    return any(p.quantity > 0 for p in positions)


def has_open_orders(open_orders: list[dict]) -> bool:
    return len(open_orders) > 0


def calc_order_quantity(candidate: Candidate, budget_per_stock_krw: int) -> int:
    if candidate.price <= 0:
        return 0
    return max(0, budget_per_stock_krw // candidate.price)


def can_buy_candidate(candidate: Candidate, budget_per_stock_krw: int, sell_tick_offset: int) -> bool:
    qty = calc_order_quantity(candidate, budget_per_stock_krw)
    if qty <= 0:
        return False
    target_sell_price = calc_target_sell_price(candidate.expect_price, sell_tick_offset)
    return target_sell_price > candidate.price


def trim_targets(
    candidates: list[Candidate],
    max_buy_count: int,
    budget_per_stock_krw: int,
    sell_tick_offset: int,
) -> list[Candidate]:
    result = []
    for c in candidates:
        if can_buy_candidate(c, budget_per_stock_krw, sell_tick_offset):
            result.append(c)
        if len(result) >= max_buy_count:
            break
    return result
