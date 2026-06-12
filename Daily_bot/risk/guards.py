from __future__ import annotations

from datetime import datetime

from Daily_bot.models import Candidate, Position
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price


def has_position(positions: list[Position]) -> bool:
    return any(p.quantity > 0 for p in positions)


def has_open_orders(open_orders: list[dict]) -> bool:
    return len(open_orders) > 0


def calc_order_quantity(candidate: Candidate, budget_per_stock_krw: int) -> int:
    if candidate.price <= 0:
        return 0
    return max(0, budget_per_stock_krw // candidate.price)


def can_buy_candidate(candidate: Candidate, budget_per_stock_krw: int, sell_tick_offset: int) -> bool:
    if candidate.price <= 0:
        return False
    if budget_per_stock_krw > 0:
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
        if max_buy_count > 0 and len(result) >= max_buy_count:
            break
    return result


def resolve_equal_slot_budget(remaining_cash_krw: int, remaining_slots: int, max_budget_per_stock_krw: int = 0) -> int:
    """Return the buy budget for the next slot using equal remaining-slot splitting."""
    if remaining_cash_krw <= 0 or remaining_slots <= 0:
        return 0
    per_slot_budget = remaining_cash_krw // remaining_slots
    if max_budget_per_stock_krw > 0:
        return min(per_slot_budget, max_budget_per_stock_krw)
    return per_slot_budget


def select_affordable_targets(
    candidates: list[Candidate],
    max_buy_count: int,
    available_cash_krw: int,
    budget_per_stock_krw: int,
    sell_tick_offset: int,
) -> list[Candidate]:
    if available_cash_krw <= 0:
        return []

    eligible = trim_targets(candidates, 0, budget_per_stock_krw, sell_tick_offset)
    if not eligible:
        return []

    desired_max = len(eligible) if max_buy_count <= 0 else min(max_buy_count, len(eligible))
    for target_count in range(desired_max, 0, -1):
        remaining_cash = available_cash_krw
        selected: list[Candidate] = []
        for candidate in eligible:
            remaining_slots = target_count - len(selected)
            per_stock_budget = resolve_equal_slot_budget(remaining_cash, remaining_slots, budget_per_stock_krw)

            qty = calc_order_quantity(candidate, per_stock_budget)
            estimated_cost = qty * candidate.price
            if qty <= 0 or estimated_cost <= 0 or estimated_cost > remaining_cash:
                continue

            selected.append(candidate)
            remaining_cash -= estimated_cost

        if len(selected) == target_count:
            return selected

    return []