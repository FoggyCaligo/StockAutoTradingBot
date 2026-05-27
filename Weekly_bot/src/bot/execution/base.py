from __future__ import annotations

from abc import ABC, abstractmethod

from bot.models import OrderIntent, Position


class OrderExecutor(ABC):
    @abstractmethod
    def get_available_cash(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, order: OrderIntent) -> str:
        raise NotImplementedError
