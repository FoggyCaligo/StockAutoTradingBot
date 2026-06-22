from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

from bot.config import StrategyConfig
from bot.data.base import MarketDataProvider
from bot.execution.base import OrderExecutor
from bot.models import Candidate, ExitDecision, OrderExecutionResult, Position
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
        self._append_tracking_candidates(candidates)
        return candidates

    def monday_buy(self) -> list[str]:
        return self.fill_entry_slots()

    def fill_entry_slots(self) -> list[str]:
        candidates = self.scan_candidates()
        positions = self.executor.get_positions()
        held_codes = {position.code for position in positions if position.quantity > 0}
        open_slots = self._available_entry_slots(candidates, positions)
        buy_candidates = [candidate for candidate in candidates if candidate.snapshot.code not in held_codes]
        if open_slots <= 0:
            self._write_today_buy_candidates([], [])
            self._write_runtime_event(
                "weekly_buy",
                "",
                "BUY",
                "SKIPPED",
                f"no_entry_slots_available current_positions={len(held_codes)} max_positions={self.config.max_positions}",
            )
            return []

        cash = self.executor.get_available_cash()
        orders = self.sizer.build_buy_orders(
            buy_candidates,
            cash,
            max_orders=open_slots,
            order_type="MARKET",
            reason="weekly_pullback_entry",
        )
        self._write_today_buy_candidates(buy_candidates, orders)
        if not orders:
            self._write_runtime_event(
                "weekly_buy",
                "",
                "BUY",
                "SKIPPED",
                f"no_affordable_order open_slots={open_slots} deposit_cash={cash} held_codes={len(held_codes)}",
            )
            return []

        order_ids: list[str] = []
        for order in orders:
            result = self._submit_order_with_logging("weekly_buy", order)
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

    def monitor_exits_loop(self) -> list[str]:
        all_submitted: list[str] = []
        while self._is_before_time(self.config.monitor_end_time):
            submitted = self.monitor_exits()
            all_submitted.extend(submitted)
            if submitted:
                self._write_runtime_event(
                    "monitor_loop",
                    "",
                    "",
                    "SUBMITTED",
                    f"submitted_exit_orders={len(submitted)}",
                )
            time.sleep(max(self.config.monitor_poll_seconds, 1))
        self._write_runtime_event(
            "monitor_loop",
            "",
            "",
            "STOPPED",
            f"monitor_end_time_reached={self.config.monitor_end_time}",
        )
        return all_submitted

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

    def _available_entry_slots(self, candidates: list[Candidate], positions: list[Position]) -> int:
        active_position_count = sum(1 for position in positions if position.quantity > 0)
        if self.config.max_positions <= 0:
            return max(len(candidates) - active_position_count, 0)
        return max(self.config.max_positions - active_position_count, 0)

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

    @staticmethod
    def _is_before_time(hhmm: str) -> bool:
        now = datetime.now().time().replace(second=0, microsecond=0)
        hour, minute = hhmm.split(":")
        cutoff = now.replace(hour=int(hour), minute=int(minute))
        return now <= cutoff

    def _write_candidates(self, candidates: list[Candidate]) -> None:
        path = self.log_dir / "candidate_tracking.csv"
        exists = path.exists()
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "time",
                    "rank",
                    "code",
                    "name",
                    "score",
                    "current_price",
                    "target_buy_price",
                    "change_pct",
                    "market_cap_krw",
                    "turnover_krw",
                    "spread_ticks",
                    "reasons",
                ],
            )
            if not exists:
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
                        "target_buy_price": s.current_price,
                        "change_pct": s.change_pct,
                        "market_cap_krw": s.market_cap_krw,
                        "turnover_krw": s.turnover_krw,
                        "spread_ticks": s.spread_ticks,
                        "reasons": "|".join(c.reasons),
                    }
                )

    def _append_tracking_candidates(self, candidates: list[Candidate]) -> None:
        self._write_candidates(candidates)

    def _write_today_buy_candidates(self, candidates: list[Candidate], orders: list) -> None:
        order_map = {order.code: order for order in orders}
        path = self.log_dir / "today_buy_candidates.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "time",
                    "rank",
                    "code",
                    "name",
                    "score",
                    "signal_price",
                    "target_buy_price",
                    "quantity",
                    "estimated_cost",
                    "included_for_buy",
                    "reasons",
                ],
            )
            writer.writeheader()
            for idx, candidate in enumerate(candidates, start=1):
                snapshot = candidate.snapshot
                order = order_map.get(snapshot.code)
                target_buy_price = int(order.reference_price) if order else snapshot.current_price
                quantity = int(order.quantity) if order else 0
                writer.writerow(
                    {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "rank": idx,
                        "code": snapshot.code,
                        "name": snapshot.name,
                        "score": round(candidate.score, 4),
                        "signal_price": snapshot.current_price,
                        "target_buy_price": target_buy_price,
                        "quantity": quantity,
                        "estimated_cost": target_buy_price * quantity,
                        "included_for_buy": "Y" if order else "N",
                        "reasons": "|".join(candidate.reasons),
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
