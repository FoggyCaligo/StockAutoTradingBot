from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.data.live_provider import LiveKrxMarketDataProvider
from bot.execution.kiwoom_real import KiwoomRealExecutor
from bot.integrations.kiwoom_client import KiwoomClient
from bot.utils import get_tick_size


def test_live_provider_uses_weekly_bot_kiwoom_client():
    provider = LiveKrxMarketDataProvider.__new__(LiveKrxMarketDataProvider)
    provider.client = KiwoomClient.__new__(KiwoomClient)
    assert provider.client.__class__.__module__ == "bot.integrations.kiwoom_client"


def test_real_executor_uses_weekly_bot_kiwoom_client():
    executor = KiwoomRealExecutor.__new__(KiwoomRealExecutor)
    executor.client = KiwoomClient.__new__(KiwoomClient)
    assert executor.client.__class__.__module__ == "bot.integrations.kiwoom_client"


def test_tick_size_available_without_daily_bot():
    assert get_tick_size(900) == 1
    assert get_tick_size(70000) == 100
