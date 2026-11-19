from types import SimpleNamespace

import pytest

from Daily_bot.broker import kiwoom_client as module
from Daily_bot.broker.kiwoom_client import KiwoomClient


def _clocked_client(monkeypatch, base_url="https://api.kiwoom.com", failures=()):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(module.time, "sleep", lambda duration: setattr(clock, "now", clock.now + duration))
    monkeypatch.setenv("KIWOOM_RATE_LIMIT_PER_SECOND", "5")
    client = KiwoomClient(base_url=base_url)
    client.access_token = "test-token"
    requests = []
    errors = iter(failures)

    def request(*_args, **kwargs):
        requests.append((kwargs["headers"]["api-id"], clock.now, kwargs["headers"]["cont-yn"]))
        error = next(errors, None)
        if error is not None:
            raise error
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"return_code": 0})

    monkeypatch.setattr(client.session, "request", request)
    return client, requests


def test_balance_limit_and_global_limit_both_hold_when_trs_are_interleaved(monkeypatch):
    client, calls = _clocked_client(monkeypatch)
    for api_id in ["kt00018", "ka10004", "kt00018", "ka10004", "kt00018"]:
        client._request("POST", "/api/test", api_id=api_id)
    times = [at for _, at, _ in calls]
    assert times == pytest.approx([100.0, 100.2, 101.0, 101.2, 102.0])
    assert all(later - earlier >= 0.2 - 1e-9 for earlier, later in zip(times, times[1:]))


def test_balance_pagination_is_also_limited(monkeypatch):
    client, calls = _clocked_client(monkeypatch)
    client._request("POST", "/api/test", api_id="kt00018")
    client._request("POST", "/api/test", api_id="kt00018", cont_yn="Y", next_key="page2")
    assert calls[1][1] - calls[0][1] == pytest.approx(1.0)
    assert calls[1][2] == "Y"


def test_retries_respect_the_balance_limit(monkeypatch):
    import requests

    client, calls = _clocked_client(monkeypatch, failures=[requests.exceptions.ConnectionError("temporary")])
    client._request("POST", "/api/test", api_id="kt00018")
    assert len(calls) == 2
    assert calls[1][1] - calls[0][1] == pytest.approx(1.0)


def test_mock_server_limits_each_tr_without_delaying_other_trs_to_one_second(monkeypatch):
    client, calls = _clocked_client(monkeypatch, base_url="https://mockapi.kiwoom.com")
    for api_id in ["ka10004", "kt00001", "ka10004", "kt00001"]:
        client._request("POST", "/api/test", api_id=api_id)
    assert [at for _, at, _ in calls] == pytest.approx([100.0, 100.2, 101.0, 101.2])


def test_regular_live_tr_retains_five_per_second(monkeypatch):
    client, calls = _clocked_client(monkeypatch)
    for _ in range(3):
        client._request("POST", "/api/test", api_id="ka10004")
    assert [at for _, at, _ in calls] == pytest.approx([100.0, 100.2, 100.4])


def test_environment_cannot_raise_global_limit_above_policy(monkeypatch):
    monkeypatch.setenv("KIWOOM_RATE_LIMIT_PER_SECOND", "100")
    assert KiwoomClient()._limiter.interval == pytest.approx(0.2)
