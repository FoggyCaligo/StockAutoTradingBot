from __future__ import annotations

import time
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
