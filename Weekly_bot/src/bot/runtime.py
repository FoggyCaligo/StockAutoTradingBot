from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from bot.config import StrategyConfig
from bot.data.base import MarketDataProvider
from bot.execution.base import OrderExecutor
from bot.models import Candidate, ExitDecision
from bot.risk.position_sizing import EqualWeightPositionSizer
from bot.strategy.weekly_pullback import WeeklyPullbackStrategy


class BotRuntime:
    def __init__(
        self,
        config: StrategyConfig,
        data_provider: MarketDataProvider,
        executor: OrderExecutor,
        log_dir: str | Path = "logs",
    ):
        self.config = config
        self.data_provider = data_provider
        self.executor = executor
        self.strategy = WeeklyPullbackStrategy(config)
        self.sizer = EqualWeightPositionSizer(config)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def scan_candidates(self) -> list[Candidate]:
        snapshots = self.data_provider.load_snapshots()
        candidates = self.strategy.select_candidates(snapshots)
        self._write_candidates(candidates)
        return candidates

    def monday_buy(self) -> list[str]:
        candidates = self.scan_candidates()
        cash = self.executor.get_available_cash()
        orders = self.sizer.build_buy_orders(candidates, cash)
        order_ids = [self.executor.submit_order(order) for order in orders]
        return order_ids

    def monitor_exits(self) -> list[str]:
        positions = self.executor.get_positions()
        submitted: list[str] = []
        decisions: list[ExitDecision] = []
        for position in positions:
            snapshot = self.data_provider.get_snapshot(position.code)
            if snapshot is None:
                continue
            decision = self.strategy.check_exit(position, snapshot.current_price)
            decisions.append(decision)
            if decision.should_sell:
                order = self.sizer.build_market_sell_order(
                    code=position.code,
                    name=position.name,
                    quantity=position.quantity,
                    reason=decision.reason,
                    reference_price=snapshot.current_price,
                )
                submitted.append(self.executor.submit_order(order))
        self._write_exit_decisions(decisions)
        return submitted

    def friday_liquidate(self) -> list[str]:
        positions = self.executor.get_positions()
        submitted: list[str] = []
        for position in positions:
            snapshot = self.data_provider.get_snapshot(position.code)
            reference_price = snapshot.current_price if snapshot else int(position.avg_price)
            order = self.sizer.build_market_sell_order(
                code=position.code,
                name=position.name,
                quantity=position.quantity,
                reason="friday_liquidation",
                reference_price=reference_price,
            )
            submitted.append(self.executor.submit_order(order))
        return submitted

    def _write_candidates(self, candidates: list[Candidate]) -> None:
        path = self.log_dir / "candidates.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "time",
                    "rank",
                    "code",
                    "name",
                    "score",
                    "current_price",
                    "change_pct",
                    "market_cap_krw",
                    "turnover_krw",
                    "spread_ticks",
                    "reasons",
                ],
            )
            writer.writeheader()
            for idx, c in enumerate(candidates, start=1):
                s = c.snapshot
                writer.writerow(
                    {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "rank": idx,
                        "code": s.code,
                        "name": s.name,
                        "score": round(c.score, 4),
                        "current_price": s.current_price,
                        "change_pct": s.change_pct,
                        "market_cap_krw": s.market_cap_krw,
                        "turnover_krw": s.turnover_krw,
                        "spread_ticks": s.spread_ticks,
                        "reasons": "|".join(c.reasons),
                    }
                )

    def _write_exit_decisions(self, decisions: list[ExitDecision]) -> None:
        path = self.log_dir / "exit_decisions.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["time", "code", "name", "quantity", "avg_price", "current_price", "pnl_pct", "should_sell", "reason"],
            )
            writer.writeheader()
            for d in decisions:
                p = d.position
                writer.writerow(
                    {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "code": p.code,
                        "name": p.name,
                        "quantity": p.quantity,
                        "avg_price": p.avg_price,
                        "current_price": d.current_price,
                        "pnl_pct": round(d.pnl_pct, 4),
                        "should_sell": d.should_sell,
                        "reason": d.reason,
                    }
                )
