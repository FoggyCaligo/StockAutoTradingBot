from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from bot.execution.base import OrderExecutor
from bot.integrations.kiwoom_client import KiwoomClient
from bot.models import OrderExecutionResult, OrderIntent, Position


class KiwoomRealExecutor(OrderExecutor):
    ORDER_FILL_TIMEOUT_SECONDS = 30
    ORDER_FILL_POLL_SECONDS = 2

    def __init__(self, log_dir: str | Path = "logs"):
        load_dotenv()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.orders_path = self.log_dir / "orders.csv"
        self.positions_path = self.log_dir / "positions.csv"

        self.client = KiwoomClient()
        self.client.auth()

    def get_available_cash(self) -> int:
        return self.client.get_orderable_cash()

    def get_positions(self) -> list[Position]:
        positions = [self._map_position(p) for p in self.client.get_positions() if getattr(p, "quantity", 0) > 0]
        self._write_positions_snapshot(positions)
        return positions

    def submit_order(self, order: OrderIntent) -> OrderExecutionResult:
        if order.quantity <= 0:
            raise ValueError(f"Order quantity must be positive: {order}")

        submitted_at = datetime.now()
        try:
            if order.side == "BUY":
                submitted_order = self.client.buy_market(order.code, order.quantity)
            elif order.side == "SELL":
                submitted_order = self.client.sell_market(order.code, order.quantity)
            else:
                raise ValueError(f"Unsupported order side: {order.side}")
        except Exception as exc:
            error_result = OrderExecutionResult(
                order_id="",
                code=order.code,
                side=order.side,
                requested_quantity=order.quantity,
                status="ERROR",
                message=str(exc),
                recorded_at=submitted_at,
            )
            self._append_order_log(order, error_result)
            raise

        order_id = submitted_order.order_id or f"KIWOOM-{submitted_at.strftime('%Y%m%d%H%M%S%f')}"
        submitted_result = OrderExecutionResult(
            order_id=order_id,
            code=order.code,
            side=order.side,
            requested_quantity=order.quantity,
            status="SUBMITTED",
            message=submitted_order.status,
            recorded_at=submitted_at,
        )
        self._append_order_log(order, submitted_result)

        executed_result = self._wait_for_execution(order, order_id, submitted_order.status)
        self._append_order_log(order, executed_result)
        return executed_result

    def recheck_account_state(self) -> tuple[list[Position], str]:
        positions = self.get_positions()
        message = f"positions={len(positions)}"
        try:
            open_orders = self.client.get_open_orders()
            message = f"{message} open_orders={len(open_orders)}"
        except Exception as exc:
            message = f"{message} open_orders_error={exc}"
        return positions, message

    def _wait_for_execution(self, order: OrderIntent, order_id: str, submit_message: str) -> OrderExecutionResult:
        deadline = time.monotonic() + self.ORDER_FILL_TIMEOUT_SECONDS
        latest_message = submit_message

        while time.monotonic() < deadline:
            fill = self.client.get_order_fill(order_id) if hasattr(self.client, "get_order_fill") else self.client.get_buy_fill(order_id)
            if fill is not None:
                return OrderExecutionResult(
                    order_id=order_id,
                    code=order.code,
                    side=order.side,
                    requested_quantity=order.quantity,
                    status="FILLED" if fill.quantity >= order.quantity else "PARTIAL_FILL",
                    filled_quantity=fill.quantity,
                    fill_price=float(fill.price),
                    message="fill_confirmed_from_order_status",
                    recorded_at=datetime.now(),
                )

            try:
                raw_status = self.client.get_order_status(order_id)
                latest_message = self._extract_status_message(raw_status) or latest_message
            except Exception as exc:
                latest_message = f"status_poll_error={exc}"

            time.sleep(self.ORDER_FILL_POLL_SECONDS)

        positions, state_message = self.recheck_account_state()
        position_match = next((p for p in positions if p.code == order.code), None)

        if order.side == "BUY" and position_match is not None and position_match.quantity > 0:
            return OrderExecutionResult(
                order_id=order_id,
                code=order.code,
                side=order.side,
                requested_quantity=order.quantity,
                status="POSITION_CONFIRMED_AFTER_TIMEOUT",
                filled_quantity=position_match.quantity,
                fill_price=float(position_match.avg_price),
                message=state_message,
                recorded_at=datetime.now(),
            )

        if order.side == "SELL" and position_match is None:
            return OrderExecutionResult(
                order_id=order_id,
                code=order.code,
                side=order.side,
                requested_quantity=order.quantity,
                status="POSITION_CLEARED_AFTER_TIMEOUT",
                filled_quantity=order.quantity,
                message=state_message,
                recorded_at=datetime.now(),
            )

        return OrderExecutionResult(
            order_id=order_id,
            code=order.code,
            side=order.side,
            requested_quantity=order.quantity,
            status="UNFILLED_TIMEOUT",
            message=f"{latest_message}; {state_message}",
            recorded_at=datetime.now(),
        )

    def _append_order_log(self, order: OrderIntent, result: OrderExecutionResult) -> None:
        exists = self.orders_path.exists()
        with self.orders_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "time",
                    "order_id",
                    "code",
                    "name",
                    "side",
                    "quantity",
                    "order_type",
                    "reason",
                    "reference_price",
                    "status",
                    "filled_quantity",
                    "fill_price",
                    "message",
                ],
            )
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "time": (result.recorded_at or datetime.now()).isoformat(timespec="seconds"),
                    "order_id": result.order_id,
                    "code": order.code,
                    "name": order.name,
                    "side": order.side,
                    "quantity": order.quantity,
                    "order_type": order.order_type,
                    "reason": order.reason,
                    "reference_price": order.reference_price,
                    "status": result.status,
                    "filled_quantity": result.filled_quantity,
                    "fill_price": result.fill_price,
                    "message": result.message,
                }
            )

    def _write_positions_snapshot(self, positions: list[Position]) -> None:
        with self.positions_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["code", "name", "quantity", "avg_price", "entry_time"])
            writer.writeheader()
            for position in positions:
                writer.writerow(
                    {
                        "code": position.code,
                        "name": position.name,
                        "quantity": position.quantity,
                        "avg_price": position.avg_price,
                        "entry_time": position.entry_time.isoformat(timespec="seconds") if position.entry_time else "",
                    }
                )

    @staticmethod
    def _extract_status_message(raw_status: object) -> str:
        if not isinstance(raw_status, dict):
            return ""
        for key in ("return_msg", "msg1", "msg", "message"):
            value = raw_status.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _map_position(position) -> Position:
        raw = getattr(position, "raw", None) or {}
        name = str(raw.get("stk_nm") or raw.get("name") or getattr(position, "ticker", "")).strip()
        return Position(
            code=getattr(position, "ticker"),
            name=name or getattr(position, "ticker"),
            quantity=int(getattr(position, "quantity")),
            avg_price=float(getattr(position, "avg_price")),
            entry_time=None,
        )
