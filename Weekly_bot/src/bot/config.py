from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StrategyConfig:
    strategy_name: str
    universe: str
    min_market_cap_krw: int
    min_change_pct: float
    max_change_pct: float
    min_turnover_krw: int
    min_volume: int
    envelope_ma_days: int
    envelope_lower_pct: float
    max_spread_ticks: int
    ma_short_days: int
    ma_mid_days: int
    ma_long_days: int
    slope_lookback_days: int
    deploy_cash_ratio: float
    max_positions: int
    min_positions: int
    take_profit_pct: float
    stop_loss_pct: float
    friday_liquidation_time: str


def load_config(path: str | Path = "config/strategy.yaml") -> StrategyConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    filters = raw["filters"]
    ma = raw["moving_average"]
    sizing = raw["position_sizing"]
    exits = raw["exit_rules"]

    return StrategyConfig(
        strategy_name=raw["strategy_name"],
        universe=raw["universe"],
        min_market_cap_krw=int(filters["min_market_cap_krw"]),
        min_change_pct=float(filters["min_change_pct"]),
        max_change_pct=float(filters["max_change_pct"]),
        min_turnover_krw=int(filters["min_turnover_krw"]),
        min_volume=int(filters["min_volume"]),
        envelope_ma_days=int(filters["envelope_ma_days"]),
        envelope_lower_pct=float(filters["envelope_lower_pct"]),
        max_spread_ticks=int(filters["max_spread_ticks"]),
        ma_short_days=int(ma["short_days"]),
        ma_mid_days=int(ma["mid_days"]),
        ma_long_days=int(ma["long_days"]),
        slope_lookback_days=int(ma["slope_lookback_days"]),
        deploy_cash_ratio=float(sizing["deploy_cash_ratio"]),
        max_positions=int(sizing["max_positions"]),
        min_positions=int(sizing["min_positions"]),
        take_profit_pct=float(exits["take_profit_pct"]),
        stop_loss_pct=float(exits["stop_loss_pct"]),
        friday_liquidation_time=str(exits["friday_liquidation_time"]),
    )
