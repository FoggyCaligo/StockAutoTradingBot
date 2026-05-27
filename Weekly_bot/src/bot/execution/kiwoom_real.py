from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from bot.execution.base import OrderExecutor
from bot.models import OrderIntent, Position


def _load_daily_bot_client_class():
    weekly_root = Path(__file__).resolve().parents[3]
    workspace_root = weekly_root.parent
    daily_bot_root = workspace_root / "Daily_bot"
    if str(daily_bot_root) not in sys.path:
        sys.path.insert(0, str(daily_bot_root))

    from broker.kiwoom_client import KiwoomClient  # type: ignore

    return KiwoomClient


class KiwoomRealExecutor(OrderExecutor):
    def __init__(self, log_dir: str | Path = "logs"):
        load_dotenv()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.orders_path = self.log_dir / "orders.csv"
        self.positions_path = self.log_dir / "positions.csv"

        client_class = _load_daily_bot_client_class()
        self.client = client_class()
        self.client.auth()

    def get_available_cash(self) -> int:
        return self.client.get_orderable_cash()

    def get_positions(self) -> list[Position]:
        positions = [self._map_position(p) for p in self.client.get_positions() if getattr(p, "quantity", 0) > 0]
        self._write_positions_snapshot(positions)
        return positions

    def submit_order(self, order: OrderIntent) -> str:
        if order.quantity <= 0:
            raise ValueError(f"Order quantity must be positive: {order}")

        if order.side == "BUY":
            result = self.client.buy_market(order.code, order.quantity)
        elif order.side == "SELL":
            result = self.client.sell_market(order.code, order.quantity)
        else:
            raise ValueError(f"Unsupported order side: {order.side}")

        order_id = result.order_id or f"KIWOOM-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self._append_order_log(order_id, order, result.status)
        return order_id

    def _append_order_log(self, order_id: str, order: OrderIntent, status: str) -> None:
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
                ],
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
                    "status": status,
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
