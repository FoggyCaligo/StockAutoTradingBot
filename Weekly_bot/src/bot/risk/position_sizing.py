from __future__ import annotations

from bot.config import StrategyConfig
from bot.models import Candidate, OrderIntent


class EqualWeightPositionSizer:
    def __init__(self, config: StrategyConfig):
        self.config = config

    def build_buy_orders(self, candidates: list[Candidate], available_cash: int) -> list[OrderIntent]:
        if not candidates:
            return []

        deploy_cash = int(available_cash * self.config.deploy_cash_ratio)
        if deploy_cash <= 0:
            return []

        max_target_count = len(candidates)
        if self.config.max_positions > 0:
            max_target_count = min(max_target_count, self.config.max_positions)

        selected = self._select_affordable_candidates(candidates, deploy_cash, max_target_count)
        if not selected:
            return []

        orders: list[OrderIntent] = []
        remaining_cash = deploy_cash
        remaining_slots = len(selected)
        for candidate in selected:
            s = candidate.snapshot
            if s.current_price <= 0 or remaining_slots <= 0:
                continue
            per_stock_cash = remaining_cash // remaining_slots
            quantity = per_stock_cash // s.current_price
            if quantity <= 0:
                remaining_slots -= 1
                continue
            estimated_cost = quantity * s.current_price
            orders.append(
                OrderIntent(
                    code=s.code,
                    name=s.name,
                    side="BUY",
                    quantity=quantity,
                    order_type="MARKET",
                    reason="weekly_pullback_entry",
                    reference_price=s.current_price,
                )
            )
            remaining_cash -= estimated_cost
            remaining_slots -= 1
        return orders

    def _select_affordable_candidates(
        self,
        candidates: list[Candidate],
        deploy_cash: int,
        max_target_count: int,
    ) -> list[Candidate]:
        if deploy_cash <= 0 or max_target_count <= 0:
            return []

        min_target_count = max(int(self.config.min_positions), 1)
        desired_max = min(max_target_count, len(candidates))
        soft_floor = min(min_target_count, desired_max)
        for target_count in range(desired_max, soft_floor - 1, -1):
            remaining_cash = deploy_cash
            selected: list[Candidate] = []
            for candidate in candidates:
                price = int(candidate.snapshot.current_price)
                if price <= 0:
                    continue
                remaining_slots = target_count - len(selected)
                if remaining_slots <= 0:
                    break
                per_stock_cash = remaining_cash // remaining_slots
                quantity = per_stock_cash // price
                estimated_cost = quantity * price
                if quantity <= 0 or estimated_cost <= 0 or estimated_cost > remaining_cash:
                    continue
                selected.append(candidate)
                remaining_cash -= estimated_cost
            if len(selected) == target_count:
                return selected

        for target_count in range(soft_floor - 1, 0, -1):
            remaining_cash = deploy_cash
            selected: list[Candidate] = []
            for candidate in candidates:
                price = int(candidate.snapshot.current_price)
                if price <= 0:
                    continue
                remaining_slots = target_count - len(selected)
                if remaining_slots <= 0:
                    break
                per_stock_cash = remaining_cash // remaining_slots
                quantity = per_stock_cash // price
                estimated_cost = quantity * price
                if quantity <= 0 or estimated_cost <= 0 or estimated_cost > remaining_cash:
                    continue
                selected.append(candidate)
                remaining_cash -= estimated_cost
            if len(selected) == target_count:
                return selected
        return []

    @staticmethod
    def build_market_sell_order(code: str, name: str, quantity: int, reason: str, reference_price: int) -> OrderIntent:
        return OrderIntent(
            code=code,
            name=name,
            side="SELL",
            quantity=quantity,
            order_type="MARKET",
            reason=reason,
            reference_price=reference_price,
        )
