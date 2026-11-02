from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import PreconditionFailed

from src.engine import Monitor, check_rule
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


def test_history_failure_still_persists_failed_state(settings, rule):
    monitor = make_monitor(settings, rule)
    monitor.store.save_run.side_effect = RuntimeError("BigQuery unavailable")
    with pytest.raises(RuntimeError, match="BigQuery unavailable"):
        monitor.run("history-failure", datetime.now(UTC))
    saved = monitor.state.finish.call_args.args[1]
    assert saved["status"] == "FAILED"
    assert saved["rule_count"] == 1
    assert saved["finished_at"] is not None


def test_original_failure_survives_secondary_persistence_failure(settings, rule):
    monitor = make_monitor(settings, rule)
    monitor.store.read_active.side_effect = ValueError("Invalid metadata")
    monitor.store.save_run.side_effect = RuntimeError("BigQuery unavailable")
    with pytest.raises(ValueError, match="Invalid metadata"):
        monitor.run("original-failure", datetime.now(UTC))
    assert monitor.state.finish.call_args.args[1]["status"] == "FAILED"


def test_results_are_written_in_one_batch(settings, rule):
    monitor = make_monitor(settings, rule)
    second = rule.model_copy(update={"rule_id": "orders-two"})
    monitor.store.read_active.side_effect = [
        [{"profile_id": "ops", "email_addresses": ["ops@example.com"]}],
        [rule.model_dump(), second.model_dump()],
    ]
    record = monitor.run("batch", datetime.now(UTC))
    monitor.store.save_results.assert_called_once()
    assert len(monitor.store.save_results.call_args.args[0]) == 2
    assert record["rule_count"] == 2


def test_empty_configuration_is_not_reported_as_healthy(settings, rule):
    monitor = make_monitor(settings, rule)
    monitor.store.read_active.side_effect = [[], []]
    assert monitor.run("empty", datetime.now(UTC))["status"] == "NEEDS_ATTENTION"


def test_query_errors_make_run_need_attention(settings, rule):
    monitor = make_monitor(settings, rule)
    monitor.store.execute_kpi.side_effect = RuntimeError()
    result = monitor.run("query-error", datetime.now(UTC))
    assert result["status"] == "NEEDS_ATTENTION"
    assert result["problem_count"] == 1


def test_disabled_email_is_distinct_from_preview(settings, rule):
    monitor = make_monitor(settings, rule)
    result = monitor.run("disabled", datetime.now(UTC), preview=False)
    assert result["notifications"][0]["status"] == "DISABLED"
    monitor.sender.send.assert_not_called()


def test_failed_artifact_does_not_skip_next_profile(settings, rule):
    settings.notifications_enabled = True
    rule.target_profiles = ["ops", "data"]
    monitor = make_monitor(settings, rule)
    monitor.store.read_active.side_effect = [
        [
            {"profile_id": "ops", "email_addresses": ["ops@example.com"]},
            {"profile_id": "data", "email_addresses": ["data@example.com"]},
        ],
        [rule.model_dump()],
    ]
    monitor.state.save_report.side_effect = [RuntimeError(), None]
    result = monitor.run("report-failure", datetime.now(UTC), preview=False)
    assert [item["status"] for item in result["notifications"]] == ["FAILED", "SENT"]
    monitor.sender.send.assert_called_once()
    assert monitor.state.checkpoint.call_count == 2


def test_partial_results_survive_run_budget_expiry(settings, rule, monkeypatch):
    monitor = make_monitor(settings, rule)
    second = rule.model_copy(update={"rule_id": "orders-two"})
    monitor.store.read_active.side_effect = [
        [{"profile_id": "ops", "email_addresses": ["ops@example.com"]}],
        [rule.model_dump(), second.model_dump()],
    ]
    # Start, first rule, check start/end, then the second-rule deadline.
    monkeypatch.setattr("src.engine.time.monotonic", Mock(side_effect=[0, 1, 1, 2, 2000]))
    with pytest.raises(TimeoutError):
        monitor.run("budget", datetime.now(UTC))
    assert len(monitor.store.save_results.call_args.args[0]) == 1
    assert monitor.state.finish.call_args.args[1]["rule_count"] == 1
