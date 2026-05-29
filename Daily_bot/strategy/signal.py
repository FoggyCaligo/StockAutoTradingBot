from __future__ import annotations

from Daily_bot.models import Candidate, HogaSnapshot
from Daily_bot.strategy.orderbook_predictor import calc_spread_percent, calc_target_sell_price, predict_price_from_hoga


def calc_expected_return(candidate: Candidate, snapshot: HogaSnapshot, sell_tick_offset: int = 1) -> Candidate:
    expect_price = predict_price_from_hoga(snapshot)
    target_sell_price = calc_target_sell_price(expect_price, sell_tick_offset)
    price = snapshot.current_price or candidate.price
    candidate.price = price
    candidate.expect_price = expect_price
    candidate.expect_revenue_percent = ((target_sell_price - price) / price * 100) if price > 0 else 0.0
    candidate.spread_percent = calc_spread_percent(snapshot)
    candidate.hoga_snapshot_time = snapshot.captured_at
    candidate.raw_hoga = snapshot.raw
    return candidate


def get_candidates_top(candidates: list[Candidate], ratio: float) -> list[Candidate]:
    ranked = sorted(candidates, key=lambda c: c.expect_revenue_percent, reverse=True)
    count = max(1, int(len(ranked) * ratio))
    return ranked[:count]


def final_filter(
    candidates: list[Candidate],
    min_expected_return_percent: float,
    sell_tick_offset: int,
    max_spread_percent: float = 0.0,
) -> list[Candidate]:
    result: list[Candidate] = []
    for c in candidates:
        if not c.trend_ok:
            continue
        if c.expect_revenue_percent < min_expected_return_percent:
            continue
        if max_spread_percent > 0 and c.spread_percent > max_spread_percent:
            continue
        target_sell_price = calc_target_sell_price(c.expect_price, sell_tick_offset)
        if target_sell_price <= c.price:
            continue
        result.append(c)
    return result
