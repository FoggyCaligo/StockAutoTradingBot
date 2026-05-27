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
        per_stock_cash = deploy_cash // len(candidates)

        orders: list[OrderIntent] = []
        for candidate in candidates:
            s = candidate.snapshot
            if s.current_price <= 0:
                continue
            quantity = per_stock_cash // s.current_price
            if quantity <= 0:
                continue
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
        return orders

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
