from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Daily_bot import main, market_trace_collector, session_slot_runner
from Daily_bot.broker.kiwoom_client import KiwoomClient
from Daily_bot.broker.mock_client import MockKiwoomClient


@pytest.mark.parametrize("environment", ["true", "false"])
def test_explicit_trade_permission_overrides_environment(monkeypatch, environment):
    monkeypatch.setenv("is_real_trade", environment)
    live = main.build_client(False, enable_trading=True)
    live._request = Mock(return_value={"ord_no": "BUY-1"})
    assert live.buy_limit("005930", 1, 70000).order_id == "BUY-1"
    live._request.assert_called_once()

    readonly = main.build_client(False, enable_trading=False)
    readonly._request = Mock(side_effect=AssertionError("Unexpected order request"))
    assert readonly.buy_limit("005930", 1, 70000).status == "BLOCKED_BY_IS_REAL_TRADE"
    readonly.cancel_order("BUY-1", ticker="005930", quantity=1)
    readonly._request.assert_not_called()


@pytest.mark.parametrize("enabled", [None, True, False])
def test_mock_mode_never_constructs_live_client(monkeypatch, enabled):
    monkeypatch.setattr(main, "KiwoomClient", Mock(side_effect=AssertionError("Live client")))
    assert isinstance(main.build_client(True, enable_trading=enabled), MockKiwoomClient)


@pytest.mark.parametrize("arguments", [["--dry-run", "--real"], ["--enable-trading"], ["--dry-run", "--enable-trading"]])
def test_rejects_conflicting_execution_modes(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["bot", *arguments])
    with pytest.raises(SystemExit) as exc:
        main.parse_args()
    assert exc.value.code == 2


@pytest.mark.parametrize("arguments,configured_dry_run,expected", [
    (["--real", "--enable-trading"], False, "live"),
    (["--real"], False, "collector"),
    (["--dry-run"], False, "mock"),
    ([], True, "mock"),
])
def test_session_routes_to_requested_mode(monkeypatch, arguments, configured_dry_run, expected):
    monkeypatch.setenv("is_real_trade", "false")
    monkeypatch.setattr("sys.argv", ["bot", *arguments])
    monkeypatch.setattr(main, "load_yaml", lambda _: {"risk": {"dry_run": configured_dry_run}})
    monkeypatch.setattr(session_slot_runner, "install_daily_slot_closure_policy", lambda: SimpleNamespace(
        session_date="2026-11-19", closed_slot_count=0, peak_active_count=0,
    ))
    run = Mock()
    collect = Mock()
    monkeypatch.setattr(main, "run", run)
    monkeypatch.setattr(market_trace_collector, "run", collect)

    session_slot_runner.main()

    if expected == "collector":
        collect.assert_called_once()
        run.assert_not_called()
    else:
        run.assert_called_once()
        collect.assert_not_called()
        assert run.call_args.kwargs["enable_trading"] is (True if expected == "live" else None)
        assert run.call_args.args[1] is (False if expected == "live" else True if arguments else None)


def test_direct_main_without_trade_permission_uses_collector(monkeypatch):
    monkeypatch.setattr(main, "load_yaml", lambda _: {"risk": {"dry_run": False}})
    monkeypatch.setenv("is_real_trade", "false")
    collect = Mock()
    monkeypatch.setattr(market_trace_collector, "run", collect)
    monkeypatch.setattr(main, "Recorder", Mock(side_effect=AssertionError("Trading startup")))
    main.run("settings.yaml")
    collect.assert_called_once_with("settings.yaml", dry_run=False)
