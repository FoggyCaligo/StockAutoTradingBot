from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.config import load_config
from bot.models import Candidate, MarketSnapshot
from bot.risk.position_sizing import EqualWeightPositionSizer


def _candidate(code: str, price: int, score: float) -> Candidate:
    snapshot = MarketSnapshot(
        code=code,
        name=code,
        is_kospi200=True,
        market_cap_krw=400_000_000_000,
        current_price=price,
        change_pct=-3.0,
        turnover_krw=10_000_000_000,
        volume=100000,
        ma20=110.0,
        ma30=120.0,
        ma30_prev=119.0,
        ma50=130.0,
        ma50_prev=129.0,
        ma120=140.0,
        ma120_prev=139.0,
        bid_price_1=price - 1,
        ask_price_1=price,
        tick_size=1,
    )
    return Candidate(snapshot=snapshot, score=score, reasons=["ok"])


def test_build_buy_orders_uses_dynamic_affordable_count():
    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    sizer = EqualWeightPositionSizer(config)
    candidates = [
        _candidate("000001", 600_000, 10.0),
        _candidate("000002", 600_000, 9.0),
        _candidate("000003", 600_000, 8.0),
    ]

    orders = sizer.build_buy_orders(candidates, available_cash=1_000_000)

    assert [order.code for order in orders] == ["000001"]
    assert orders[0].quantity == 1


def test_build_buy_orders_evenly_redistributes_remaining_cash():
    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    sizer = EqualWeightPositionSizer(config)
    candidates = [
        _candidate("000001", 300_000, 10.0),
        _candidate("000002", 300_000, 9.0),
        _candidate("000003", 300_000, 8.0),
    ]

    orders = sizer.build_buy_orders(candidates, available_cash=1_000_000)

    assert [order.code for order in orders] == ["000001", "000002", "000003"]
    assert [order.quantity for order in orders] == [1, 1, 1]


def test_build_buy_orders_respects_max_positions_cap():
    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, max_positions=2, min_positions=1)
    sizer = EqualWeightPositionSizer(config)
    candidates = [
        _candidate("000001", 100_000, 10.0),
        _candidate("000002", 100_000, 9.0),
        _candidate("000003", 100_000, 8.0),
    ]

    orders = sizer.build_buy_orders(candidates, available_cash=1_000_000)

    assert [order.code for order in orders] == ["000001", "000002"]


def test_build_buy_orders_falls_back_below_soft_minimum_position_count():
    config = load_config(ROOT / "config/strategy.yaml")
    sizer = EqualWeightPositionSizer(config)
    candidates = [
        _candidate("000001", 100_000, 10.0),
        _candidate("000002", 100_000, 9.0),
        _candidate("000003", 100_000, 8.0),
        _candidate("000004", 100_000, 7.0),
    ]

    orders = sizer.build_buy_orders(candidates, available_cash=10_000_000)

    assert [order.code for order in orders] == ["000001", "000002", "000003", "000004"]


def test_build_buy_orders_limits_new_orders_to_open_slots():
    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, max_positions=3, min_positions=1)
    sizer = EqualWeightPositionSizer(config)
    candidates = [
        _candidate("000001", 100_000, 10.0),
        _candidate("000002", 100_000, 9.0),
        _candidate("000003", 100_000, 8.0),
    ]

    orders = sizer.build_buy_orders(candidates, available_cash=1_000_000, max_orders=1)

    assert [order.code for order in orders] == ["000001"]


def test_build_buy_orders_excludes_already_held_codes_when_topping_up():
    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, max_positions=3, min_positions=1)
    sizer = EqualWeightPositionSizer(config)
    candidates = [
        _candidate("000001", 100_000, 10.0),
        _candidate("000002", 100_000, 9.0),
        _candidate("000003", 100_000, 8.0),
    ]

    orders = sizer.build_buy_orders(
        candidates,
        available_cash=1_000_000,
        max_orders=2,
        excluded_codes={"000001"},
    )

    assert [order.code for order in orders] == ["000002", "000003"]
