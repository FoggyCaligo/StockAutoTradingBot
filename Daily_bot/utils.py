from __future__ import annotations

import time
import math
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_hhmm(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(hour=int(hour), minute=int(minute))


def now_time() -> dtime:
    return datetime.now().time().replace(second=0, microsecond=0)


def is_between_now(start: str, end: str) -> bool:
    n = now_time()
    return parse_hhmm(start) <= n <= parse_hhmm(end)


def is_after_now(threshold: str) -> bool:
    return now_time() >= parse_hhmm(threshold)


class RateLimiter:
    def __init__(self, per_second: int):
        self.interval = 1.0 / max(per_second, 1)
        self._last_call = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.monotonic()


def get_tick_size(price: int) -> int:
    # KRX 호가단위는 시장/가격대/시점에 따라 변경될 수 있으므로 실전 전 최신 기준 확인 필요.
    if price < 1000:
        return 1
    if price < 5000:
        return 5
    if price < 10000:
        return 10
    if price < 50000:
        return 50
    if price < 100000:
        return 100
    if price < 500000:
        return 500
    return 1000


def round_to_tick(price: int) -> int:
    tick = get_tick_size(price)
    return int(price // tick * tick)


def move_price_by_ticks(price: int, tick_count: int) -> int:
    if price <= 0 or tick_count == 0:
        return max(0, price)

    moved_price = int(price)
    if tick_count > 0:
        for _ in range(tick_count):
            moved_price += get_tick_size(moved_price)
        return moved_price

    for _ in range(abs(tick_count)):
        step = get_tick_size(max(0, moved_price - 1))
        moved_price = max(0, moved_price - step)
    return moved_price


def count_ticks_between_prices(start_price: int, end_price: int) -> int:
    if start_price <= 0 or end_price <= start_price:
        return 0

    tick_count = 0
    current_price = int(start_price)
    while current_price < end_price:
        next_price = move_price_by_ticks(current_price, 1)
        if next_price <= current_price:
            break
        current_price = next_price
        tick_count += 1
    return tick_count


def ceil_tick_count(value: float) -> int:
    return max(0, int(math.ceil(value)))
