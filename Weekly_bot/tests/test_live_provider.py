from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.data.live_provider import LiveKrxMarketDataProvider
from bot.models import MarketSnapshot


def _snapshot(price: int) -> MarketSnapshot:
    return MarketSnapshot(
        code="005930",
        name="삼성전자",
        is_kospi200=True,
        market_cap_krw=400_000_000_000,
        current_price=price,
        change_pct=-3.0,
        turnover_krw=10_000_000_000,
        volume=100000,
        ma20=75000,
        ma30=76000,
        ma30_prev=75500,
        ma50=77000,
        ma50_prev=76500,
        ma120=68000,
        ma120_prev=67500,
        bid_price_1=price - 100,
        ask_price_1=price,
        tick_size=100,
    )


def test_get_snapshot_refreshes_live_price_even_when_cache_exists():
    provider = LiveKrxMarketDataProvider.__new__(LiveKrxMarketDataProvider)
    provider._snapshots = [_snapshot(70000)]
    provider._universe_df = None
    provider._universe_rows_by_code = {}
    provider._history_cache = {}
    provider._get_universe_row = lambda code: pd.Series({"Code": code, "Name": "삼성전자"})  # type: ignore[method-assign]
    provider._build_snapshot = lambda row, code: _snapshot(72500)  # type: ignore[method-assign]

    refreshed = provider.get_snapshot("005930")

    assert refreshed is not None
    assert refreshed.current_price == 72500
    assert provider._snapshots[0].current_price == 72500
