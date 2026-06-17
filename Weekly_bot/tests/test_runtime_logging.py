from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dataclasses import replace

from bot.config import load_config
from bot.data.base import MarketDataProvider
from bot.execution.base import OrderExecutor
from bot.models import MarketSnapshot, OrderExecutionResult, OrderIntent, Position
from bot.runtime import BotRuntime


class _DataProviderStub(MarketDataProvider):
    def __init__(self, snapshots: list[MarketSnapshot]):
        self.snapshots = snapshots

    def load_snapshots(self) -> list[MarketSnapshot]:
        return self.snapshots

    def get_snapshot(self, code: str) -> MarketSnapshot | None:
        return next((snapshot for snapshot in self.snapshots if snapshot.code == code), None)


class _ExecutorStub(OrderExecutor):
    def __init__(self, results: list[OrderExecutionResult], positions: list[Position] | None = None):
        self.results = results
        self.positions = positions or []

    def get_available_cash(self) -> int:
        return 1_000_000

    def get_positions(self) -> list[Position]:
        return self.positions

    def submit_order(self, order: OrderIntent) -> OrderExecutionResult:
        return self.results.pop(0)

    def recheck_account_state(self) -> tuple[list[Position], str]:
        return self.positions, "positions=0 open_orders=1"


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        code="005930",
        name="삼성전자",
        is_kospi200=True,
        market_cap_krw=400_000_000_000,
        current_price=70000,
        change_pct=-3.0,
        turnover_krw=10_000_000_000,
        volume=100000,
        ma20=75000,
        ma30=76000,
        ma30_prev=75500,
        ma50=77000,
        ma50_prev=76500,
        ma120=68000,
        ma120_prev=67500,
        bid_price_1=69900,
        ask_price_1=70000,
        tick_size=100,
    )


def test_runtime_logs_unfilled_timeout_event(tmp_path):
    base_config = load_config(ROOT / "config/strategy.yaml")
    config = replace(base_config, min_positions=1)
    provider = _DataProviderStub([_snapshot()])
    executor = _ExecutorStub(
        [
            OrderExecutionResult(
                order_id="OID-1",
                code="005930",
                side="BUY",
                requested_quantity=3,
                status="UNFILLED_TIMEOUT",
                message="timeout",
            )
        ]
    )

    runtime = BotRuntime(config=config, data_provider=provider, executor=executor, log_dir=tmp_path)
    order_ids = runtime.monday_buy()

    assert order_ids == ["OID-1"]
    runtime_events = (tmp_path / "runtime_events.csv").read_text(encoding="utf-8")
    assert "UNFILLED_TIMEOUT" in runtime_events
    assert "positions=0 open_orders=1" in runtime_events
