from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from bot.execution.base import OrderExecutor
from bot.models import OrderIntent, Position


class DryRunExecutor(OrderExecutor):
    def __init__(self, available_cash: int, log_dir: str | Path = "logs"):
        self.available_cash = available_cash
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.positions_path = self.log_dir / "positions.csv"
        self.orders_path = self.log_dir / "orders.csv"

    def get_available_cash(self) -> int:
        return self.available_cash

    def get_positions(self) -> list[Position]:
        if not self.positions_path.exists():
            return []
        positions: list[Position] = []
        with self.positions_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                positions.append(
                    Position(
                        code=row["code"],
                        name=row["name"],
                        quantity=int(row["quantity"]),
                        avg_price=float(row["avg_price"]),
                        entry_time=datetime.fromisoformat(row["entry_time"]) if row.get("entry_time") else None,
                    )
                )
        return positions

    def submit_order(self, order: OrderIntent) -> str:
        order_id = f"DRY-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self._append_order_log(order_id, order)
        if order.side == "BUY":
            self._append_position(order)
        elif order.side == "SELL":
            self._remove_position(order.code)
        return order_id

    def _append_order_log(self, order_id: str, order: OrderIntent) -> None:
        exists = self.orders_path.exists()
        with self.orders_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["time", "order_id", "code", "name", "side", "quantity", "order_type", "reason", "reference_price"],
            )
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "order_id": order_id,
                    "code": order.code,
                    "name": order.name,
                    "side": order.side,
                    "quantity": order.quantity,
                    "order_type": order.order_type,
                    "reason": order.reason,
                    "reference_price": order.reference_price,
                }
            )

    def _append_position(self, order: OrderIntent) -> None:
        existing = [p for p in self.get_positions() if p.code != order.code]
        existing.append(
            Position(
                code=order.code,
                name=order.name,
                quantity=order.quantity,
                avg_price=float(order.reference_price),
                entry_time=datetime.now(),
            )
        )
        self._write_positions(existing)

    def _remove_position(self, code: str) -> None:
        self._write_positions([p for p in self.get_positions() if p.code != code])

    def _write_positions(self, positions: list[Position]) -> None:
        with self.positions_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["code", "name", "quantity", "avg_price", "entry_time"])
            writer.writeheader()
            for p in positions:
                writer.writerow(
                    {
                        "code": p.code,
                        "name": p.name,
                        "quantity": p.quantity,
                        "avg_price": p.avg_price,
                        "entry_time": p.entry_time.isoformat(timespec="seconds") if p.entry_time else "",
                    }
                )
