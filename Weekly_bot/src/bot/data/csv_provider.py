from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from bot.data.base import MarketDataProvider, PositionProvider
from bot.models import MarketSnapshot, Position


class CsvMarketDataProvider(MarketDataProvider):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._snapshots: list[MarketSnapshot] | None = None

    def load_snapshots(self) -> list[MarketSnapshot]:
        if self._snapshots is not None:
            return self._snapshots

        df = pd.read_csv(self.path)
        snapshots: list[MarketSnapshot] = []
        for row in df.to_dict(orient="records"):
            snapshots.append(
                MarketSnapshot(
                    code=str(row["code"]).zfill(6),
                    name=str(row["name"]),
                    is_kospi200=bool(row["is_kospi200"]),
                    market_cap_krw=int(row["market_cap_krw"]),
                    current_price=int(row["current_price"]),
                    change_pct=float(row["change_pct"]),
                    turnover_krw=int(row["turnover_krw"]),
                    volume=int(row["volume"]),
                    ma20=float(row["ma20"]),
                    ma30=float(row["ma30"]),
                    ma30_prev=float(row["ma30_prev"]),
                    ma50=float(row["ma50"]),
                    ma50_prev=float(row["ma50_prev"]),
                    ma120=float(row["ma120"]),
                    ma120_prev=float(row["ma120_prev"]),
                    bid_price_1=int(row["bid_price_1"]),
                    ask_price_1=int(row["ask_price_1"]),
                    tick_size=int(row["tick_size"]),
                )
            )
        self._snapshots = snapshots
        return snapshots

    def get_snapshot(self, code: str) -> MarketSnapshot | None:
        code = str(code).zfill(6)
        return next((s for s in self.load_snapshots() if s.code == code), None)


class CsvPositionProvider(PositionProvider):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load_positions(self) -> list[Position]:
        if not self.path.exists():
            return []
        df = pd.read_csv(self.path)
        positions: list[Position] = []
        for row in df.to_dict(orient="records"):
            entry_time = None
            if row.get("entry_time"):
                try:
                    entry_time = datetime.fromisoformat(str(row["entry_time"]))
                except ValueError:
                    entry_time = None
            positions.append(
                Position(
                    code=str(row["code"]).zfill(6),
                    name=str(row["name"]),
                    quantity=int(row["quantity"]),
                    avg_price=float(row["avg_price"]),
                    entry_time=entry_time,
                )
            )
        return positions
