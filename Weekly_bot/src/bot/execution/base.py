from __future__ import annotations

from abc import ABC, abstractmethod

from bot.models import OrderExecutionResult, OrderIntent, Position


class OrderExecutor(ABC):
    @abstractmethod
    def get_available_cash(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, order: OrderIntent) -> OrderExecutionResult:
        raise NotImplementedError

    @abstractmethod
    def recheck_account_state(self) -> tuple[list[Position], str]:
        raise NotImplementedError
