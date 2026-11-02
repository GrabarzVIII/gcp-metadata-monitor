from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import PreconditionFailed

from src.engine import Monitor, check_rule
from src.main import app
from src.models import Profile
from src.report_generator import render_report
from src.state import RunAlreadyClaimed, RunState


def make_monitor(settings, rule):
    store, logs, state, sender = Mock(), Mock(), Mock(), Mock()
    store.read_active.side_effect = [
        [{"profile_id": "ops", "email_addresses": ["ops@example.com"]}],
        [rule.model_dump()],
    ]
    store.execute_kpi.return_value = 200
    state.claim.return_value = None
    return Monitor(settings, store, logs, state, sender)


def test_preview_never_sends(settings, rule):
    settings.notifications_enabled = True
    monitor = make_monitor(settings, rule)
    record = monitor.run("preview", datetime.now(UTC))
    assert record["status"] == "COMPLETED"
    monitor.sender.send.assert_not_called()
    monitor.state.save_report.assert_called_once()
    monitor.store.save_results.assert_called_once()


def test_completed_duplicate_does_not_repeat_checks(settings, rule):
    monitor = make_monitor(settings, rule)
    monitor.state.claim.return_value = {"status": "COMPLETED"}
    monitor.run("duplicate", datetime.now(UTC))
    monitor.store.read_active.assert_not_called()


def test_notification_failure_is_visible(settings, rule):
    settings.notifications_enabled = True
    monitor = make_monitor(settings, rule)
    monitor.sender.send.side_effect = RuntimeError()
    record = monitor.run("live", datetime.now(UTC), preview=False)
    assert record["status"] == "NEEDS_ATTENTION"
    assert record["notifications"][0]["status"] == "FAILED"


def test_invalid_rule_does_not_skip_valid_rule(settings, rule):
    monitor = make_monitor(settings, rule)
    monitor.store.read_active.side_effect = [
        [{"profile_id": "ops", "email_addresses": ["ops@example.com"]}],
        [{"rule_id": "invalid"}, rule.model_dump()],
    ]
    record = monitor.run("config", datetime.now(UTC))
    assert record["configuration_errors"] == ["invalid"]
    assert record["rule_count"] == 1


def test_routing_and_html_escaping(rule):
    rule.rule_name = "<script>alert(1)</script>"
    store = Mock()
    store.execute_kpi.return_value = 200
    result = check_rule(rule, "run", datetime.now(UTC), store, Mock())
    wrong = Profile(profile_id="other", email_addresses=["other@example.com"])
    assert render_report(wrong, [result], "UTC") is None
    right = Profile(profile_id="ops", email_addresses=["ops@example.com"])
    html = render_report(right, [result], "UTC")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    right.only_problems = True
    assert render_report(right, [result], "UTC") is None


def test_atomic_claim_uses_generation_precondition():
    client = Mock()
    state = RunState("state-bucket", client)
    state.claim("test")
    blob = client.bucket.return_value.blob.return_value
    assert blob.upload_from_string.call_args.kwargs["if_generation_match"] == 0


def test_incomplete_claim_blocks_blind_retry():
    client = Mock()
    blob = client.bucket.return_value.blob.return_value
    blob.upload_from_string.side_effect = PreconditionFailed("exists")
    blob.download_as_text.return_value = '{"status":"RUNNING"}'
    with pytest.raises(RunAlreadyClaimed):
        RunState("state-bucket", client).claim("test")


def test_health_requires_no_cloud_credentials():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
