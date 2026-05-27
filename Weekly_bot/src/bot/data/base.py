from __future__ import annotations

from abc import ABC, abstractmethod

from bot.models import MarketSnapshot, Position


class MarketDataProvider(ABC):
    @abstractmethod
    def load_snapshots(self) -> list[MarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_snapshot(self, code: str) -> MarketSnapshot | None:
        raise NotImplementedError


class PositionProvider(ABC):
    @abstractmethod
    def load_positions(self) -> list[Position]:
        raise NotImplementedError
