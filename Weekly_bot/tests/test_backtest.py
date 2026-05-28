from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.backtest import BacktestSettings, WeeklyBacktester
from bot.config import load_config
from bot.data.historical_provider import HistoricalKrxDataProvider, HistoricalMarketData


def _build_history() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=140)
    rows: list[dict[str, float]] = []
    close = 80.0
    for date in dates[:-5]:
        open_price = close
        high = close * 1.01
        low = close * 0.99
        rows.append(
            {
                "Open": open_price,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": 12_000_000,
                "Change": 0.002,
            }
        )
        close += 0.2

    weekly_rows = [
        {"Open": 102.0, "High": 103.0, "Low": 100.0, "Close": 101.0, "Volume": 15_000_000, "Change": -0.05},
        {"Open": 101.0, "High": 103.0, "Low": 100.5, "Close": 102.0, "Volume": 13_000_000, "Change": 0.0099},
        {"Open": 102.0, "High": 106.0, "Low": 101.0, "Close": 105.0, "Volume": 13_000_000, "Change": 0.0294},
        {"Open": 105.0, "High": 106.0, "Low": 103.0, "Close": 104.0, "Volume": 11_000_000, "Change": -0.0095},
        {"Open": 104.0, "High": 105.0, "Low": 103.0, "Close": 104.5, "Volume": 11_000_000, "Change": 0.0048},
    ]
    rows.extend(weekly_rows)
    return pd.DataFrame(rows, index=dates)


def test_weekly_backtester_runs_and_writes_outputs(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    histories = {"005930": _build_history()}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)

    config = load_config(ROOT / "config/strategy.yaml")
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-01",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            output_dir=tmp_path,
        ),
    )

    artifacts = backtester.run()

    assert not artifacts.summary.empty
    assert not artifacts.trades.empty
    assert artifacts.trades.iloc[0]["exit_reason"] == "take_profit"
    assert artifacts.trades.iloc[0]["signal_date"] == "2024-07-08"
    assert artifacts.trades.iloc[0]["entry_date"] == "2024-07-09"
    assert int(artifacts.trades.iloc[0]["holding_days"]) == 1
    assert float(artifacts.summary.iloc[0]["ending_cash"]) > 1_000_000
    assert (tmp_path / "summary.csv").exists()
    assert (tmp_path / "trades.csv").exists()


def test_same_day_collision_can_prefer_take_profit(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    histories = {"005930": _build_history()}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)

    config = load_config(ROOT / "config/strategy.yaml")
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-01",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            collision_take_profit_ratio=1.0,
            output_dir=tmp_path,
        ),
    )

    artifacts = backtester.run()

    assert "take_profit" in str(artifacts.trades.iloc[0]["exit_reason"])


def test_unreliable_monday_approx_falls_back_to_friday_signal(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    histories = {"005930": _build_history()}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)

    config = load_config(ROOT / "config/strategy.yaml")
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-01",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            approximate_monday_10am=True,
            monday_approx_max_gap_pct=0.5,
            output_dir=tmp_path,
        ),
    )

    artifacts = backtester.run()

    assert artifacts.trades.iloc[0]["signal_mode"] == "fallback"


def test_mid_price_approximation_is_supported():
    config = load_config(ROOT / "config/strategy.yaml")
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-01-01",
            end="2024-01-31",
            initial_cash=1_000_000,
            approximate_monday_10am=True,
            monday_approx_price_mode="mid",
        ),
    )

    assert backtester._approximate_signal_price(100.0, 90.0) == 95


def test_liquidation_offset_extends_holding_window(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    history = _build_history().copy()
    history.loc[pd.Timestamp("2024-07-09"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [101.0, 102.0, 100.5, 101.8, 13_000_000, 0.0079]
    history.loc[pd.Timestamp("2024-07-10"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [101.8, 102.4, 101.0, 102.0, 13_000_000, 0.002]
    history.loc[pd.Timestamp("2024-07-11"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [102.0, 102.8, 101.7, 102.4, 11_000_000, 0.0039]
    history.loc[pd.Timestamp("2024-07-12"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [102.4, 102.9, 102.0, 102.5, 11_000_000, 0.001]
    history.loc[pd.Timestamp("2024-07-15"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [102.6, 103.0, 102.1, 102.8, 12_000_000, 0.0029]
    histories = {"005930": history}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)

    config = load_config(ROOT / "config/strategy.yaml")
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-01",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            liquidation_offset_trading_days=1,
            output_dir=tmp_path,
        ),
    )

    artifacts = backtester.run()

    assert artifacts.trades.iloc[0]["exit_date"] == "2024-07-15"
    assert artifacts.trades.iloc[0]["exit_reason"] == "extended_liquidation"
