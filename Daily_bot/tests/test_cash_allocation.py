from datetime import datetime

from dataclasses import dataclass

from Daily_bot.main import (
    activate_buy,
    estimate_account_value,
    get_external_cash_flow_since,
    is_daily_loss_limit_reached,
    poll_and_record_new_fills,
    reconcile_broker_fills,
    resolve_buy_count,
    resolve_empty_slots,
    resolve_total_slot_count,
    resolve_target_budget_per_stock,
)
from Daily_bot.models import Candidate, Fill, OrderResult


@dataclass
class _RecorderStub:
    orders: list[OrderResult]
    fills: list[tuple[Fill, str, str]] | None = None

    def save_order(self, order: OrderResult) -> None:
        self.orders.append(order)

    def save_fill(self, fill, side: str, source: str = "broker") -> None:
        if self.fills is None:
            self.fills = []
        self.fills.append((fill, side, source))

    def get_orders_needing_fill_poll(self):
        return []

    def get_fill_index(self, session_date: str):
        return {}

    def replace_fill(self, fill, side: str, source: str = "broker") -> None:
        self.save_fill(fill, side=side, source=source)

    def rebuild_session_fill_exports(self, session_date: str) -> None:
        self.rebuilt_session_date = session_date

    def has_recorded_sell_fill_after(
        self,
        ticker: str,
        created_at: str | None,
        exclude_order_id: str,
        minimum_quantity: int = 1,
    ) -> bool:
        return False


class _ClientStub:
    def __init__(self, orderable_cash: int):
        self.orderable_cash = orderable_cash
        self.orderable_cash_sequence: list[int] | None = None
        self.fill_sequence: list[Fill | None] | None = None
        self.buy_fill_sequence: list[Fill | None] | None = None
        self.sell_limit_error: RuntimeError | None = None
        self.positions = []
        self.open_orders = []
        self.buy_calls: list[tuple[str, int, int]] = []
        self.sell_calls: list[tuple[str, int, int]] = []
        self.market_sell_calls: list[tuple[str, int]] = []
        self.cancel_calls: list[tuple[str, str, int]] = []
        self.sell_fill_sequence: list[Fill | None] | None = None

    def get_orderable_cash(self) -> int:
        if self.orderable_cash_sequence:
            return self.orderable_cash_sequence.pop(0)
        return self.orderable_cash

    def buy_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        self.buy_calls.append((ticker, quantity, price))
        return OrderResult(order_id=f"BUY-{len(self.buy_calls)}", ticker=ticker, side="BUY", quantity=quantity, price=price, status="SUBMITTED")

    def wait_buy_filled(
        self,
        order_id: str,
        expected_quantity: int | None = None,
        timeout_seconds: int = 30,
    ) -> Fill | None:
        if self.fill_sequence:
            return self.fill_sequence.pop(0)
        return Fill(order_id=order_id, ticker="MOCK", quantity=1, price=10000)

    def get_buy_fill(self, order_id: str) -> Fill | None:
        if self.buy_fill_sequence:
            return self.buy_fill_sequence.pop(0)
        return None

    def cancel_order(self, order_id: str, ticker: str = "", quantity: int = 0) -> None:
        self.cancel_calls.append((order_id, ticker, quantity))

    def wait_until_order_cancelled(self, order_id: str, timeout_seconds: int = 30) -> bool:
        return True

    def sell_limit(self, ticker: str, quantity: int, price: int) -> OrderResult:
        if self.sell_limit_error is not None:
            raise self.sell_limit_error
        self.sell_calls.append((ticker, quantity, price))
        return OrderResult(order_id=f"SELL-{len(self.sell_calls)}", ticker=ticker, side="SELL", quantity=quantity, price=price, status="SUBMITTED")

    def sell_market(self, ticker: str, quantity: int) -> OrderResult:
        self.market_sell_calls.append((ticker, quantity))
        return OrderResult(order_id=f"MSELL-{len(self.market_sell_calls)}", ticker=ticker, side="SELL", quantity=quantity, price=0, status="FILLED")

    def get_order_fill(self, order_id: str, ticker: str = "", side: str = "") -> Fill | None:
        if self.sell_fill_sequence:
            return self.sell_fill_sequence.pop(0)
        return None

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return self.open_orders


def _cfg() -> dict:
    return {
        "api": {"order_rate_limit_per_second": 1000},
        "risk": {
            "max_budget_per_stock_krw": 100_000,
            "max_budget_per_cycle_krw": 150_000,
        },
        "strategy": {"sell_tick_offset": 1},
    }


def _unlimited_cfg() -> dict:
    return {
        "api": {"order_rate_limit_per_second": 1000},
        "risk": {
            "max_budget_per_stock_krw": 0,
            "max_budget_per_cycle_krw": 0,
        },
        "strategy": {"sell_tick_offset": 1},
    }


def test_activate_buy_uses_orderable_cash_and_cycle_budget():
    client = _ClientStub(orderable_cash=120_000)
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _cfg())

    assert client.buy_calls == [
        ("005930", 10, 10_000),
        ("000660", 2, 10_000),
    ]


def test_activate_buy_skips_when_orderable_cash_is_zero():
    client = _ClientStub(orderable_cash=0)
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [Candidate(ticker="005930", price=10_000, expect_price=10_200)]

    activate_buy(client, recorder, targets, _cfg())

    assert client.buy_calls == []


def test_activate_buy_refreshes_cash_after_unfilled_buy_cancel():
    client = _ClientStub(orderable_cash=120_000)
    client.orderable_cash_sequence = [120_000, 120_000]
    client.fill_sequence = [
        None,
        Fill(order_id="BUY-2", ticker="000660", quantity=10, price=10_000),
    ]
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _cfg())

    assert client.cancel_calls == [("BUY-1", "005930", 10)]
    assert client.buy_calls == [
        ("005930", 10, 10_000),
        ("000660", 10, 10_000),
    ]


def test_activate_buy_places_exit_order_for_partial_fill_after_cancel():
    client = _ClientStub(orderable_cash=120_000)
    client.orderable_cash_sequence = [120_000]
    client.fill_sequence = [None]
    client.buy_fill_sequence = [Fill(order_id="BUY-1", ticker="005930", quantity=3, price=10_000)]
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [Candidate(ticker="005930", price=10_000, expect_price=10_200)]

    activate_buy(client, recorder, targets, _cfg())

    assert client.cancel_calls == [("BUY-1", "005930", 10)]
    assert client.sell_calls == [("005930", 3, 10150)]


def test_activate_buy_uses_buy_limit_price_when_partial_fill_price_is_anomalously_high():
    client = _ClientStub(orderable_cash=120_000)
    client.orderable_cash_sequence = [120_000]
    client.fill_sequence = [None]
    client.buy_fill_sequence = [Fill(order_id="BUY-1", ticker="005930", quantity=3, price=10_300, raw={"cntr_pric": "10300"})]
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [Candidate(ticker="005930", price=10_000, expect_price=10_200)]

    activate_buy(client, recorder, targets, _cfg())

    assert client.cancel_calls == [("BUY-1", "005930", 10)]
    assert client.sell_calls == [("005930", 3, 10150)]
    assert client.market_sell_calls == []


def test_activate_buy_distributes_full_cash_across_all_targets_when_limits_removed():
    client = _ClientStub(orderable_cash=300_000)
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
        Candidate(ticker="035420", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _unlimited_cfg())

    assert client.buy_calls == [
        ("005930", 10, 10_000),
        ("000660", 10, 10_000),
        ("035420", 10, 10_000),
    ]


def test_activate_buy_stops_new_buys_after_exception_when_position_exists():
    client = _ClientStub(orderable_cash=300_000)
    client.fill_sequence = [Fill(order_id="BUY-1", ticker="005930", quantity=10, price=10_000)]
    client.sell_limit_error = RuntimeError("sell api failed")
    client.positions = [type("Position", (), {"ticker": "005930", "quantity": 10, "avg_price": 10_000})()]
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _unlimited_cfg())

    assert client.buy_calls == [("005930", 15, 10_000)]


def test_activate_buy_records_target_exit_fill_when_sell_fill_is_available():
    client = _ClientStub(orderable_cash=120_000)
    client.fill_sequence = [Fill(order_id="BUY-1", ticker="005930", quantity=10, price=10_000)]
    client.sell_fill_sequence = [Fill(order_id="SELL-1", ticker="005930", quantity=10, price=10_150)]
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [Candidate(ticker="005930", price=10_000, expect_price=10_200)]

    activate_buy(client, recorder, targets, _cfg())

    assert client.sell_calls == [("005930", 10, 10150)]
    assert recorder.fills is not None
    assert any(side == "SELL" and source == "target_exit" for _fill, side, source in recorder.fills)


def test_activate_buy_uses_session_fixed_slot_budget_per_stock():
    client = _ClientStub(orderable_cash=120_000)
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [
        Candidate(ticker="005930", price=10_000, expect_price=10_200),
        Candidate(ticker="000660", price=10_000, expect_price=10_200),
    ]

    activate_buy(client, recorder, targets, _cfg(), slot_budget_per_stock=40_000)

    assert client.buy_calls == [
        ("005930", 4, 10_000),
        ("000660", 4, 10_000),
    ]


def test_activate_buy_stops_when_session_position_limit_is_already_reached():
    client = _ClientStub(orderable_cash=300_000)
    client.positions = [
        type("Position", (), {"ticker": "005930", "quantity": 1, "avg_price": 70_000})(),
        type("Position", (), {"ticker": "000660", "quantity": 1, "avg_price": 200_000})(),
        type("Position", (), {"ticker": "035420", "quantity": 1, "avg_price": 50_000})(),
    ]
    client.open_orders = [
        {"order_id": "SELL-1", "ticker": "051910", "side": "SELL", "ord_qty": "1"},
        {"order_id": "BUY-1", "ticker": "068270", "side": "BUY", "ord_qty": "1"},
    ]
    recorder = _RecorderStub(orders=[], fills=[])
    targets = [
        Candidate(ticker="207940", price=100_000, expect_price=100_500),
        Candidate(ticker="105560", price=50_000, expect_price=50_300),
    ]

    activate_buy(client, recorder, targets, _unlimited_cfg(), position_limit=5, slot_budget_per_stock=100_000)

    assert client.buy_calls == []


def test_poll_and_record_new_fills_reconciles_missing_sell_fill_when_position_is_gone():
    client = _ClientStub(orderable_cash=0)
    client.positions = []
    client.open_orders = []
    recorder = _RecorderStub(orders=[], fills=[])

    recorder.get_orders_needing_fill_poll = lambda: [
        {
            "broker_order_id": "SELL-1",
            "ticker": "008770",
            "side": "SELL",
            "quantity": 3,
            "price": 53900,
            "recorded_fill_quantity": 0,
        }
    ]

    poll_and_record_new_fills(client, recorder)

    assert recorder.fills is not None
    assert len(recorder.fills) == 1
    fill, side, source = recorder.fills[0]
    assert side == "SELL"
    assert source == "sell_reconciliation"
    assert fill.order_id == "SELL-1"
    assert fill.ticker == "008770"
    assert fill.quantity == 3
    assert fill.price == 53900
    assert fill.raw == {"source": "sell_reconciliation", "reason": "fill_lookup_missing"}


def test_poll_and_record_new_fills_prefers_direct_sell_fill_before_reconciliation(monkeypatch):
    client = _ClientStub(orderable_cash=0)
    client.positions = []
    client.open_orders = []
    client.sell_fill_sequence = [
        None,
        Fill(order_id="SELL-1", ticker="008770", quantity=3, price=53850),
    ]
    recorder = _RecorderStub(orders=[], fills=[])

    recorder.get_orders_needing_fill_poll = lambda: [
        {
            "broker_order_id": "SELL-1",
            "ticker": "008770",
            "side": "SELL",
            "quantity": 3,
            "price": 53900,
            "recorded_fill_quantity": 0,
        }
    ]
    monkeypatch.setattr("Daily_bot.main.time.sleep", lambda _: None)

    poll_and_record_new_fills(client, recorder)

    assert recorder.fills is not None
    assert len(recorder.fills) == 1
    fill, side, source = recorder.fills[0]
    assert side == "SELL"
    assert source == "poll"
    assert fill.order_id == "SELL-1"
    assert fill.ticker == "008770"
    assert fill.quantity == 3
    assert fill.price == 53850


def test_poll_and_record_new_fills_skips_sell_reconciliation_when_replacement_sell_was_already_recorded():
    client = _ClientStub(orderable_cash=0)
    client.positions = []
    client.open_orders = []
    recorder = _RecorderStub(orders=[], fills=[])

    recorder.get_orders_needing_fill_poll = lambda: [
        {
            "broker_order_id": "SELL-1",
            "ticker": "008770",
            "side": "SELL",
            "quantity": 3,
            "price": 53900,
            "created_at": "2026-06-11 09:12:00",
            "recorded_fill_quantity": 0,
        }
    ]
    recorder.has_recorded_sell_fill_after = lambda ticker, created_at, exclude_order_id, minimum_quantity=1: True

    poll_and_record_new_fills(client, recorder)

    assert recorder.fills == []


def test_reconcile_broker_fills_replaces_existing_fill_and_rebuilds_exports():
    fill = Fill(
        order_id="SELL-1",
        ticker="008770",
        quantity=3,
        price=53900,
        filled_at=datetime.fromisoformat("2026-06-10T09:42:49"),
    )
    client = type(
        "ClientStub",
        (),
        {
            "get_grouped_fills": lambda self: [(fill, "SELL")],
        },
    )()
    recorder = _RecorderStub(orders=[], fills=[])
    recorder.get_fill_index = lambda session_date: {
        ("SELL-1", "SELL"): {
            "broker_order_id": "SELL-1",
            "side": "SELL",
            "quantity": 3,
            "price": 53900,
            "filled_at": "2026-06-10T09:57:06.263650",
            "source": "sell_reconciliation",
        }
    }

    summary = reconcile_broker_fills(client, recorder, session_date="2026-06-10")

    assert summary == {"broker_fill_count": 1, "inserted_or_updated": 1}
    assert recorder.fills is not None
    saved_fill, side, source = recorder.fills[0]
    assert saved_fill.order_id == "SELL-1"
    assert saved_fill.price == 53900
    assert saved_fill.filled_at == datetime.fromisoformat("2026-06-10T09:42:49")
    assert side == "SELL"
    assert source == "eod_reconciliation"
    assert recorder.rebuilt_session_date == "2026-06-10"


def test_estimate_account_value_includes_open_buy_orders():
    client = _ClientStub(orderable_cash=50_000)
    client.positions = [type("Position", (), {"ticker": "005930", "quantity": 2, "avg_price": 10_000})()]
    client.open_orders = [
        {"io_tp_nm": "+매수", "ord_qty": "3", "oso_qty": "3", "ord_pric": "10000"},
        {"io_tp_nm": "-매도", "ord_qty": "2", "oso_qty": "2", "ord_pric": "10150"},
    ]
    client.get_20hoga = lambda ticker: type("Snapshot", (), {"current_price": 10_500})()

    result = estimate_account_value(client, client.positions, client.open_orders)

    assert result == 50_000 + 21_000 + 30_000


def test_daily_loss_limit_ignores_cash_reserved_by_open_buy_orders():
    client = _ClientStub(orderable_cash=50_000)
    client.positions = []
    client.open_orders = [
        {"io_tp_nm": "+매수", "ord_qty": "5", "oso_qty": "5", "ord_pric": "10000"},
    ]
    cfg = {"risk": {"daily_loss_limit_percent": 10.0}}

    reached = is_daily_loss_limit_reached(client, cfg, initial_account_value=100_000, positions=client.positions, open_orders=client.open_orders)

    assert reached is False


def test_get_external_cash_flow_since_counts_only_flows_after_session_start():
    cfg = {
        "accounting": {
            "cash_flows": [
                {"effective_at": "2026-06-05T08:40:00+09:00", "amount_krw": 200_000},
                {"effective_at": "2026-06-05T10:15:00+09:00", "amount_krw": 300_000},
                {"effective_at": "2026-06-05T10:20:00+09:00", "amount_krw": -50_000},
            ]
        }
    }

    result = get_external_cash_flow_since(
        cfg,
        since=datetime.fromisoformat("2026-06-05T09:00:00+09:00"),
        until=datetime.fromisoformat("2026-06-05T10:30:00+09:00"),
    )

    assert result == 250_000


def test_daily_loss_limit_ignores_intraday_deposit_when_measuring_loss():
    client = _ClientStub(orderable_cash=130_000)
    cfg = {
        "risk": {"daily_loss_limit_percent": 10.0},
        "accounting": {
            "cash_flows": [
                {"effective_at": "2026-06-04T10:15:00+09:00", "amount_krw": 30_000},
            ]
        },
    }

    reached = is_daily_loss_limit_reached(
        client,
        cfg,
        initial_account_value=100_000,
        session_started_at=datetime.fromisoformat("2026-06-04T09:00:00+09:00"),
        positions=[],
        open_orders=[],
    )

    assert reached is False


def test_daily_loss_limit_still_triggers_after_adjusting_for_intraday_deposit():
    client = _ClientStub(orderable_cash=115_000)
    cfg = {
        "risk": {"daily_loss_limit_percent": 10.0},
        "accounting": {
            "cash_flows": [
                {"effective_at": "2026-06-04T10:15:00+09:00", "amount_krw": 30_000},
            ]
        },
    }

    reached = is_daily_loss_limit_reached(
        client,
        cfg,
        initial_account_value=100_000,
        session_started_at=datetime.fromisoformat("2026-06-04T09:00:00+09:00"),
        positions=[],
        open_orders=[],
    )

    assert reached is True


def test_slot_refill_buy_count_respects_empty_slots_when_strategy_limit_is_unlimited():
    cfg = {
        "strategy": {"max_buy_count": 0},
        "risk": {"min_slot_count": 1, "target_budget_ratio_per_stock": 0, "max_budget_per_stock_krw": 0},
    }
    empty_slots = 2
    buy_count = resolve_buy_count(cfg, empty_slots, planning_cash=500_000)

    assert buy_count == 2


def test_resolve_target_budget_per_stock_prefers_ratio_and_caps_with_max():
    cfg = {
        "risk": {"target_budget_ratio_per_stock": 0.33, "max_budget_per_stock_krw": 5_000_000},
    }

    assert resolve_target_budget_per_stock(cfg, planning_cash=210_000) == 69_300
    assert resolve_target_budget_per_stock(cfg, planning_cash=20_000_000) == 5_000_000


def test_resolve_target_budget_per_stock_prefers_slot_unit_when_configured():
    cfg = {
        "risk": {
            "min_slot_count": 3,
            "slot_budget_unit_krw": 5_000_000,
            "target_budget_ratio_per_stock": 0.33,
            "max_budget_per_stock_krw": 7_000_000,
        },
    }

    assert resolve_target_budget_per_stock(cfg, planning_cash=12_000_000) == 4_000_000
    assert resolve_target_budget_per_stock(cfg, planning_cash=20_000_000) == 5_000_000


def test_resolve_buy_count_scales_with_cash_using_slot_unit_and_min_slots():
    cfg = {
        "strategy": {"max_buy_count": 0},
        "risk": {
            "min_slot_count": 3,
            "slot_budget_unit_krw": 5_000_000,
            "max_budget_per_stock_krw": 5_000_000,
        },
    }

    assert resolve_buy_count(cfg, empty_slots=3, planning_cash=10_000_000) == 3
    assert resolve_buy_count(cfg, empty_slots=5, planning_cash=15_000_000) == 3
    assert resolve_buy_count(cfg, empty_slots=40, planning_cash=20_000_000) == 4
    assert resolve_buy_count(cfg, empty_slots=40, planning_cash=25_000_000) == 5
    assert resolve_buy_count(cfg, empty_slots=40, planning_cash=50_000_000) == 10
    assert resolve_buy_count(cfg, empty_slots=40, planning_cash=100_000_000) == 20


def test_resolve_total_slot_count_respects_slot_unit_and_min_slots():
    cfg = {
        "risk": {
            "min_slot_count": 3,
            "slot_budget_unit_krw": 5_000_000,
        },
    }

    assert resolve_total_slot_count(cfg, total_capital=12_000_000) == 3
    assert resolve_total_slot_count(cfg, total_capital=15_000_000) == 3
    assert resolve_total_slot_count(cfg, total_capital=20_000_000) == 4
    assert resolve_total_slot_count(cfg, total_capital=25_000_000) == 5


def test_resolve_buy_count_respects_empty_slots_and_explicit_max_buy_count():
    cfg = {
        "strategy": {"max_buy_count": 3},
        "risk": {
            "min_slot_count": 3,
            "target_budget_ratio_per_stock": 0.33,
            "max_budget_per_stock_krw": 5_000_000,
        },
    }

    assert resolve_buy_count(cfg, empty_slots=2, planning_cash=300_000) == 2
    assert resolve_buy_count(cfg, empty_slots=5, planning_cash=300_000) == 3


def test_resolve_empty_slots_treats_zero_position_limit_as_unlimited():
    assert resolve_empty_slots(max_position_count=0, active_count=3, candidate_count=12) == 12
    assert resolve_empty_slots(max_position_count=5, active_count=3, candidate_count=12) == 2
