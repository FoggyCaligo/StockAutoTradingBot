from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.config import load_config
from bot.data.csv_provider import CsvMarketDataProvider
from bot.strategy.weekly_pullback import WeeklyPullbackStrategy


def test_select_candidates_from_sample_data():
    config = load_config(ROOT / "config/strategy.yaml")
    provider = CsvMarketDataProvider(ROOT / "data/sample_market_snapshot.csv")
    strategy = WeeklyPullbackStrategy(config)

    candidates = strategy.select_candidates(provider.load_snapshots())

    codes = {c.snapshot.code for c in candidates}
    assert "005930" in codes
    assert "000660" in codes
    assert "068270" not in codes
    assert "051910" not in codes  # 등락률 -1% 조건 미달
    assert "123456" not in codes  # KOSPI200 아님
