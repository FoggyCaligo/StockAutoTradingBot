from __future__ import annotations

from Daily_bot.models import Candidate, HogaSnapshot
from Daily_bot.strategy.orderbook_predictor import (
    apply_orderbook_decay,
    calc_ask_depth_amount,
    calc_spread_percent,
    calc_target_sell_price,
    clamp_decay_min_weight,
    predict_price_from_hoga,
)


def calc_expected_return(
    candidate: Candidate,
    snapshot: HogaSnapshot,
    sell_tick_offset: int = 1,
    strategy_cfg: dict | None = None,
) -> Candidate:
    strategy_cfg = strategy_cfg or {}
    bid_decay_min_weight = clamp_decay_min_weight(strategy_cfg.get("orderbook_bid_linear_decay_min_weight", 1.0), default=1.0)
    ask_decay_min_weight = clamp_decay_min_weight(strategy_cfg.get("orderbook_ask_linear_decay_min_weight", 1.0), default=1.0)
    effective_snapshot = apply_orderbook_decay(
        snapshot,
        bid_min_weight=bid_decay_min_weight,
        ask_min_weight=ask_decay_min_weight,
    )

    expect_price = predict_price_from_hoga(effective_snapshot)
    target_sell_price = calc_target_sell_price(expect_price, sell_tick_offset)
    price = effective_snapshot.current_price or candidate.price
    candidate.price = price
    if candidate.prev_close_price > 0 and price > 0:
        candidate.prev_day_change_percent = ((price - candidate.prev_close_price) / candidate.prev_close_price) * 100
    candidate.expect_price = expect_price
    candidate.expect_revenue_percent = ((target_sell_price - price) / price * 100) if price > 0 else 0.0
    candidate.spread_percent = calc_spread_percent(effective_snapshot)
    candidate.ask_depth_5_amount_krw = calc_ask_depth_amount(effective_snapshot, levels=5)
    candidate.hoga_snapshot_time = effective_snapshot.captured_at
    candidate.raw_hoga = snapshot.raw
    return candidate


def get_candidates_top(candidates: list[Candidate], ratio: float) -> list[Candidate]:
    ranked = sorted(candidates, key=lambda c: c.expect_revenue_percent, reverse=True)
    count = max(1, int(len(ranked) * ratio))
    return ranked[:count]


def min_expected_return_with_spread(
    min_expected_return_percent: float,
    spread_percent: float,
    spread_expected_return_multiplier: float,
) -> float:
    if spread_percent <= 0 or spread_expected_return_multiplier <= 0:
        return min_expected_return_percent
    return max(min_expected_return_percent, spread_percent * spread_expected_return_multiplier)


def final_filter(
    candidates: list[Candidate],
    min_expected_return_percent: float,
    sell_tick_offset: int,
    max_spread_percent: float = 0.0,
    min_prev_day_change_percent: float = 0.0,
    max_prev_day_change_percent: float = 0.0,
    spread_expected_return_multiplier: float = 0.0,
) -> list[Candidate]:
    result: list[Candidate] = []
    for c in candidates:
        if not c.trend_ok:
            continue
        if min_prev_day_change_percent < 0 and c.prev_day_change_percent > min_prev_day_change_percent:
            continue
        if max_prev_day_change_percent > 0 and c.prev_day_change_percent >= max_prev_day_change_percent:
            continue
        if max_spread_percent > 0 and c.spread_percent > max_spread_percent:
            continue
        required_expected_return = min_expected_return_with_spread(
            min_expected_return_percent=min_expected_return_percent,
            spread_percent=c.spread_percent,
            spread_expected_return_multiplier=spread_expected_return_multiplier,
        )
        if c.expect_revenue_percent < required_expected_return:
            continue
        target_sell_price = calc_target_sell_price(c.expect_price, sell_tick_offset)
        if target_sell_price <= c.price:
            continue
        result.append(c)
    return result
