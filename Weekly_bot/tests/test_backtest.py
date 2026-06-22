from pathlib import Path
import sys
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.backtest import BacktestSettings, WeeklyBacktester
from dataclasses import replace

from bot.config import load_config
from bot.data.historical_provider import HistoricalKrxDataProvider, HistoricalMarketData
from bot.models import Candidate
from bot.strategy.weekly_pullback import WeeklyPullbackStrategy


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
        {"Open": 101.0, "High": 103.0, "Low": 97.0, "Close": 99.0, "Volume": 13_000_000, "Change": -0.0198},
        {"Open": 102.0, "High": 108.0, "Low": 101.0, "Close": 105.0, "Volume": 13_000_000, "Change": 0.0294},
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
    monkeypatch.setattr(
        WeeklyPullbackStrategy,
        "select_candidates",
        lambda self, snapshots: [Candidate(snapshot=s, score=1.0, reasons=["test"]) for s in snapshots if s.is_kospi200],
    )

    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-08",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            run_name="test-run",
            output_dir=tmp_path,
        ),
    )
    monkeypatch.setattr(backtester, "_load_historical_universe_codes", lambda date_key: ({"005930"}, "local"))

    artifacts = backtester.run()

    assert not artifacts.summary.empty
    assert not artifacts.trades.empty
    assert artifacts.trades.iloc[0]["exit_reason"] == "take_profit"
    assert artifacts.trades.iloc[0]["signal_date"] == "2024-07-08"
    assert artifacts.trades.iloc[0]["entry_date"] == "2024-07-09"
    assert int(artifacts.trades.iloc[0]["holding_days"]) == 0
    assert float(artifacts.summary.iloc[0]["ending_cash"]) > 1_000_000
    assert artifacts.output_dir == tmp_path / "test-run"
    assert (artifacts.output_dir / "summary.csv").exists()
    assert (artifacts.output_dir / "trades.csv").exists()
    assert (artifacts.output_dir / "weekly.csv").exists()
    assert (artifacts.output_dir / "monthly.csv").exists()
    assert (artifacts.output_dir / "universe_coverage.csv").exists()
    assert (artifacts.output_dir / "run_manifest.json").exists()
    assert (artifacts.output_dir / "config_snapshot.yaml").exists()

    manifest = json.loads((artifacts.output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_name"] == "test-run"
    assert manifest["config"]["min_positions"] == 1
    assert manifest["settings"]["signal_weekday"] == "monday"
    assert artifacts.summary.iloc[0]["run_name"] == "test-run"
    assert artifacts.summary.iloc[0]["signal_price_basis"] == "previous_close"
    assert artifacts.summary.iloc[0]["min_change_pct"] == config.min_change_pct
    assert artifacts.summary.iloc[0]["max_positions"] == config.max_positions


def test_same_day_collision_can_prefer_take_profit(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    histories = {"005930": _build_history()}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)
    monkeypatch.setattr(
        WeeklyPullbackStrategy,
        "select_candidates",
        lambda self, snapshots: [Candidate(snapshot=s, score=1.0, reasons=["test"]) for s in snapshots if s.is_kospi200],
    )

    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-08",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            collision_take_profit_ratio=1.0,
            run_name="collision-run",
            output_dir=tmp_path,
        ),
    )
    monkeypatch.setattr(backtester, "_load_historical_universe_codes", lambda date_key: ({"005930"}, "local"))

    artifacts = backtester.run()

    assert "take_profit" in str(artifacts.trades.iloc[0]["exit_reason"])


def test_unreliable_monday_approx_falls_back_to_friday_signal(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    histories = {"005930": _build_history()}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)
    monkeypatch.setattr(
        WeeklyPullbackStrategy,
        "select_candidates",
        lambda self, snapshots: [Candidate(snapshot=s, score=1.0, reasons=["test"]) for s in snapshots if s.is_kospi200],
    )

    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-08",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            approximate_monday_10am=True,
            monday_approx_max_gap_pct=0.5,
            run_name="approx-fallback-run",
            output_dir=tmp_path,
        ),
    )
    monkeypatch.setattr(backtester, "_load_historical_universe_codes", lambda date_key: ({"005930"}, "local"))

    artifacts = backtester.run()

    assert artifacts.trades.iloc[0]["signal_mode"] == "fallback"


def test_mid_price_approximation_is_supported():
    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-01-01",
            end="2024-01-31",
            initial_cash=1_000_000,
            approximate_monday_10am=True,
            monday_approx_price_mode="mid",
            run_name="mid-mode-run",
        ),
    )

    assert backtester._approximate_signal_price(100.0, 90.0) == 95


def test_liquidation_offset_extends_holding_window(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    history = _build_history().copy()
    history.loc[pd.Timestamp("2024-07-08"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [102.0, 103.0, 100.0, 101.0, 15_000_000, -0.05]
    history.loc[pd.Timestamp("2024-07-09"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [98.2, 99.4, 97.8, 99.2, 13_000_000, -0.0178]
    history.loc[pd.Timestamp("2024-07-10"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [98.4, 98.9, 98.1, 98.5, 13_000_000, 0.001]
    history.loc[pd.Timestamp("2024-07-11"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [98.5, 99.1, 98.2, 98.7, 11_000_000, 0.002]
    history.loc[pd.Timestamp("2024-07-12"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [98.7, 99.2, 98.3, 98.8, 11_000_000, 0.001]
    history.loc[pd.Timestamp("2024-07-15"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [98.8, 99.3, 98.5, 99.0, 12_000_000, 0.002]
    histories = {"005930": history}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)
    monkeypatch.setattr(
        WeeklyPullbackStrategy,
        "select_candidates",
        lambda self, snapshots: [Candidate(snapshot=s, score=1.0, reasons=["test"]) for s in snapshots if s.is_kospi200],
    )

    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-08",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            liquidation_offset_trading_days=1,
            run_name="extended-liquidation-run",
            output_dir=tmp_path,
        ),
    )
    monkeypatch.setattr(backtester, "_load_historical_universe_codes", lambda date_key: ({"005930"}, "local"))

    artifacts = backtester.run()

    assert artifacts.trades.iloc[0]["exit_date"] == "2024-07-15"
    assert artifacts.trades.iloc[0]["exit_reason"] == "extended_liquidation"


def test_entry_is_skipped_when_entry_day_close_stays_below_validation_threshold(tmp_path, monkeypatch):
    listing = pd.DataFrame([{"Code": "005930", "Name": "Samsung", "Marcap": 400_000_000_000}])
    history = _build_history().copy()
    history.loc[pd.Timestamp("2024-07-08"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [102.0, 103.0, 100.0, 101.0, 15_000_000, -0.05]
    history.loc[pd.Timestamp("2024-07-09"), ["Open", "High", "Low", "Close", "Volume", "Change"]] = [99.5, 100.0, 99.2, 99.4, 13_000_000, -0.0158]
    histories = {"005930": history}

    def _fake_load(self, start: str, end: str) -> HistoricalMarketData:
        return HistoricalMarketData(listing=listing, histories=histories)

    monkeypatch.setattr(HistoricalKrxDataProvider, "load", _fake_load)
    monkeypatch.setattr(
        WeeklyPullbackStrategy,
        "select_candidates",
        lambda self, snapshots: [Candidate(snapshot=s, score=1.0, reasons=["test"]) for s in snapshots if s.is_kospi200],
    )

    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    backtester = WeeklyBacktester(
        config=config,
        settings=BacktestSettings(
            start="2024-07-08",
            end="2024-07-31",
            initial_cash=1_000_000,
            signal_weekday="monday",
            entry_offset_trading_days=1,
            entry_trigger_change_pct=-2.0,
            run_name="entry-validation-run",
            output_dir=tmp_path,
        ),
    )
    monkeypatch.setattr(backtester, "_load_historical_universe_codes", lambda date_key: ({"005930"}, "local"))

    artifacts = backtester.run()

    assert artifacts.trades.empty
    assert int(artifacts.weekly.iloc[0]["num_orders"]) == 0
    assert int(artifacts.weekly.iloc[0]["num_trades"]) == 0


def test_period_membership_file_is_supported(tmp_path, monkeypatch):
    historical_dir = tmp_path / "historical_kospi200"
    historical_dir.mkdir()
    pd.DataFrame(
        [
            {"code": "005930", "effective_from": "2024-01-01", "effective_to": "2024-06-30"},
            {"code": "000660", "effective_from": "2024-07-01", "effective_to": "2024-12-31"},
        ]
    ).to_csv(historical_dir / "membership.csv", index=False)

    base_config = load_config(ROOT / "config/strategy.yaml")
    backtester = WeeklyBacktester(
        config=replace(base_config, min_positions=1),
        settings=BacktestSettings(
            start="2024-01-01",
            end="2024-12-31",
            initial_cash=1_000_000,
            run_name="membership-period-run",
            output_dir=tmp_path,
        ),
    )

    monkeypatch.setattr(backtester, "_historical_universe_data_dir", lambda: historical_dir)

    june_codes = backtester._load_local_historical_universe_codes(pd.Timestamp("2024-06-28"))
    july_codes = backtester._load_local_historical_universe_codes(pd.Timestamp("2024-07-01"))

    assert june_codes == {"005930"}
    assert july_codes == {"000660"}


def test_invalid_fallback_universe_is_ignored(tmp_path, monkeypatch):
    invalid_cache = tmp_path / "kospi200_latest.csv"
    pd.DataFrame({"Code": [str(idx).zfill(6) for idx in range(946)]}).to_csv(invalid_cache, index=False)

    fake_file = tmp_path / "src" / "bot" / "backtest.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("", encoding="utf-8")

    base_config = load_config(ROOT / "config/strategy.yaml")
    backtester = WeeklyBacktester(
        config=replace(base_config, min_positions=1),
        settings=BacktestSettings(
            start="2024-01-01",
            end="2024-12-31",
            initial_cash=1_000_000,
            run_name="invalid-fallback-run",
            output_dir=tmp_path,
        ),
    )

    monkeypatch.setattr(sys.modules[WeeklyBacktester.__module__], "__file__", str(fake_file))

    assert backtester._load_fallback_universe_codes({}) == set()
