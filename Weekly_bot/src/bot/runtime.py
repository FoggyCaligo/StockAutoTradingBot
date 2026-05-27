from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from bot.config import StrategyConfig
from bot.data.base import MarketDataProvider
from bot.execution.base import OrderExecutor
from bot.models import Candidate, ExitDecision, OrderExecutionResult
from bot.risk.position_sizing import EqualWeightPositionSizer
from bot.strategy.weekly_pullback import WeeklyPullbackStrategy


class BotRuntime:
    SUCCESS_STATUSES = {"FILLED", "POSITION_CONFIRMED_AFTER_TIMEOUT", "POSITION_CLEARED_AFTER_TIMEOUT"}

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
        order_ids: list[str] = []
        for order in orders:
            result = self._submit_order_with_logging("monday_buy", order)
            if result.order_id:
                order_ids.append(result.order_id)
        return order_ids

    def monitor_exits(self) -> list[str]:
        positions = self.executor.get_positions()
        submitted: list[str] = []
        decisions: list[ExitDecision] = []
        for position in positions:
            snapshot = self.data_provider.get_snapshot(position.code)
            if snapshot is None:
                self._write_runtime_event("monitor_exits", position.code, "SELL", "SNAPSHOT_MISSING", "snapshot unavailable")
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
                result = self._submit_order_with_logging("monitor_exits", order)
                if result.order_id:
                    submitted.append(result.order_id)
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
            result = self._submit_order_with_logging("friday_liquidate", order)
            if result.order_id:
                submitted.append(result.order_id)
        return submitted

    def _submit_order_with_logging(self, phase: str, order) -> OrderExecutionResult:
        try:
            result = self.executor.submit_order(order)
        except Exception as exc:
            positions, state_message = self.executor.recheck_account_state()
            self._write_runtime_event(
                phase,
                order.code,
                order.side,
                "ERROR",
                f"{exc}; {state_message}; positions={len(positions)}",
            )
            raise

        if result.status == "PARTIAL_FILL":
            self._write_runtime_event(
                phase,
                order.code,
                order.side,
                result.status,
                f"filled_quantity={result.filled_quantity} fill_price={result.fill_price}",
            )
        elif result.status not in self.SUCCESS_STATUSES:
            positions, state_message = self.executor.recheck_account_state()
            self._write_runtime_event(
                phase,
                order.code,
                order.side,
                result.status,
                f"{result.message}; {state_message}; positions={len(positions)}",
            )

        return result

    def _write_runtime_event(self, phase: str, code: str, side: str, status: str, message: str) -> None:
        path = self.log_dir / "runtime_events.csv"
        exists = path.exists()
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["time", "phase", "code", "side", "status", "message"],
            )
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "phase": phase,
                    "code": code,
                    "side": side,
                    "status": status,
                    "message": message,
                }
            )

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
