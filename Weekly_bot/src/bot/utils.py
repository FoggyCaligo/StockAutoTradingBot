from __future__ import annotations

import time


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
