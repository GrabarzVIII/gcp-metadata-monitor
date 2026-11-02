from datetime import UTC, datetime
from unittest.mock import Mock

import functions_framework
import pytest

from src.state import RunAlreadyClaimed


@pytest.fixture
def client(monkeypatch):
    # Exercise the actual Functions Framework adapter used in Cloud Run.
    app = functions_framework.create_app("run_monitoring", "main.py")
    monitor = Mock()
    monitor.run.return_value = {"status": "COMPLETED", "run_id": "test"}
    factory = Mock(return_value=monitor)
    # create_app imports the source again and registers it under its module name.
    monkeypatch.setattr(__import__("main"), "create_monitor", factory)
    return app.test_client(), monitor, factory


def test_health_does_not_initialize_cloud_clients(client):
    http, _, factory = client
    assert http.get("/healthz").json == {"status": "ok"}
    factory.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [[], None, {"preview": "false"}, {"unknown": True}, {"window_end": "2026-01-02T08:00:00"}],
)
def test_invalid_request_never_runs_monitor(client, payload):
    http, _, factory = client
    assert http.post("/", json=payload).status_code == 400
    factory.assert_not_called()


def test_malformed_json_is_rejected(client):
    http, _, factory = client
    assert http.post("/", data="{", content_type="application/json").status_code == 400
    factory.assert_not_called()


def test_post_defaults_to_preview(client):
    http, monitor, _ = client
    assert http.post("/", json={}).status_code == 200
    assert monitor.run.call_args.kwargs["preview"] is True


def test_manual_window_and_live_flag(client):
    http, monitor, _ = client
    response = http.post(
        "/run-monitoring",
        json={
            "preview": False,
            "window_end": "2026-01-02T10:00:00+02:00",
        },
    )
    assert response.status_code == 200
    assert monitor.run.call_args.args[1] == datetime(2026, 1, 2, 8, tzinfo=UTC)
    assert monitor.run.call_args.kwargs["preview"] is False


def test_scheduled_retries_use_same_key_and_observation_window(client):
    http, monitor, _ = client
    headers = {
        "X-CloudScheduler-JobName": "projects/p/jobs/daily",
        "X-CloudScheduler-ScheduleTime": "2026-01-02T08:00:00Z",
    }
    http.post("/", json={}, headers=headers)
    first = monitor.run.call_args.args
    http.post("/", json={}, headers=headers)
    assert first == monitor.run.call_args.args


@pytest.mark.parametrize(
    "headers",
    [
        {"X-CloudScheduler-JobName": "daily"},
        {"X-CloudScheduler-ScheduleTime": "2026-01-02T08:00:00Z"},
        {"X-CloudScheduler-JobName": "daily", "X-CloudScheduler-ScheduleTime": "invalid"},
        {"X-CloudScheduler-JobName": "daily", "X-CloudScheduler-ScheduleTime": "2026-01-02"},
    ],
)
def test_incomplete_or_invalid_scheduler_headers_are_rejected(client, headers):
    http, _, factory = client
    assert http.post("/", json={}, headers=headers).status_code == 400
    factory.assert_not_called()


def test_scheduled_window_override_is_rejected(client):
    http, _, factory = client
    response = http.post(
        "/",
        json={"window_end": "2026-01-03T08:00:00Z"},
        headers={
            "X-CloudScheduler-JobName": "daily",
            "X-CloudScheduler-ScheduleTime": "2026-01-02T08:00:00Z",
        },
    )
    assert response.status_code == 400
    factory.assert_not_called()


def test_repeated_manual_calls_get_distinct_ids(client):
    http, monitor, _ = client
    http.post("/", json={})
    first = monitor.run.call_args.args[0]
    http.post("/", json={})
    assert first != monitor.run.call_args.args[0]


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        ("/", "get", 405),
        ("/unknown", "post", 404),
    ],
)
def test_path_and_method_validation(client, path, method, expected):
    http, _, factory = client
    assert getattr(http, method)(path).status_code == expected
    factory.assert_not_called()


def test_duplicate_claim_is_conflict(client):
    http, monitor, _ = client
    monitor.run.side_effect = RunAlreadyClaimed()
    assert http.post("/", json={}).status_code == 409


def test_invalid_settings_return_json_error(client):
    http, _, factory = client
    factory.side_effect = RuntimeError("private configuration")
    response = http.post("/", json={})
    assert response.status_code == 500
    assert "private" not in response.get_data(as_text=True)


def test_attention_status_is_visible_to_scheduler(client):
    http, monitor, _ = client
    monitor.run.return_value = {"status": "NEEDS_ATTENTION"}
    assert http.post("/", json={}).status_code == 500
