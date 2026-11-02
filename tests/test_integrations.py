from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.bq_client import BigQueryStore
from src.logging_client import LogChecks


class Rows(list):
    schema = [Mock(name="value")]


@pytest.mark.parametrize("statement", ["SCRIPT", "DELETE", "CREATE_TABLE", None])
def test_mutating_or_unknown_sql_never_executes(settings, rule, statement):
    client = Mock()
    client.query.return_value.statement_type = statement
    backend = BigQueryStore(settings, client)
    with pytest.raises(ValueError, match="SELECT"):
        backend.execute_kpi(rule, datetime.now(UTC), datetime.now(UTC))
    assert client.query.call_count == 1


def test_expensive_sql_never_executes(settings, rule):
    client = Mock()
    client.query.return_value.statement_type = "SELECT"
    client.query.return_value.total_bytes_processed = settings.maximum_bytes_billed + 1
    with pytest.raises(ValueError, match="budget"):
        BigQueryStore(settings, client).execute_kpi(rule, datetime.now(UTC), datetime.now(UTC))
    assert client.query.call_count == 1


@pytest.mark.parametrize("values", [[], [(1,), (2,)], [("text",)], [(True,)]])
def test_scalar_contract(settings, rule, values):
    estimate = Mock(statement_type="SELECT", total_bytes_processed=0)
    rows = Rows(values)
    rows.schema = [Mock()]
    rows.schema[0].name = "value"
    job = Mock()
    job.result.return_value = rows
    client = Mock()
    client.query.side_effect = [estimate, job]
    with pytest.raises(ValueError):
        BigQueryStore(settings, client).execute_kpi(rule, datetime.now(UTC), datetime.now(UTC))


def test_query_timeout_cancels_job(settings, rule):
    estimate = Mock(statement_type="SELECT", total_bytes_processed=0)
    job = Mock()
    job.result.side_effect = TimeoutError()
    client = Mock()
    client.query.side_effect = [estimate, job]
    with pytest.raises(TimeoutError):
        BigQueryStore(settings, client).execute_kpi(rule, datetime.now(UTC), datetime.now(UTC))
    job.cancel.assert_called_once()


def test_log_limit_is_not_reported_as_exact_count(settings, rule):
    settings.log_entry_limit = 2
    client = Mock()
    client.list_log_entries.return_value = SimpleNamespace(entries=[1, 2, 3], next_page_token="")
    with pytest.raises(ValueError, match="limit"):
        LogChecks(settings, client).count(rule, datetime.now(UTC), datetime.now(UTC))


def test_log_filter_is_scoped_to_project_and_window(settings, rule):
    rule.log_filter = 'severity="ERROR" OR severity="CRITICAL"'
    client = Mock()
    client.list_log_entries.return_value = SimpleNamespace(entries=[], next_page_token="")
    now = datetime.now(UTC)
    assert LogChecks(settings, client).count(rule, now, now) == 0
    arguments = client.list_log_entries.call_args.kwargs["request"]
    assert arguments["resource_names"] == ["projects/source-project"]
    assert arguments["filter"].startswith(f"({rule.log_filter}) AND")


def test_empty_log_page_with_token_is_not_end_of_results(settings, rule):
    client = Mock()
    client.list_log_entries.side_effect = [
        SimpleNamespace(entries=[], next_page_token="next"),
        SimpleNamespace(entries=[1, 2], next_page_token=""),
    ]
    now = datetime.now(UTC)
    assert LogChecks(settings, client).count(rule, now, now) == 2
    assert client.list_log_entries.call_args.kwargs["request"]["page_token"] == "next"
    assert client.list_log_entries.call_args.kwargs["retry"] is None


def test_log_deadline_applies_across_pages(settings, rule, monkeypatch):
    client = Mock()
    client.list_log_entries.return_value = SimpleNamespace(entries=[], next_page_token="next")
    monkeypatch.setattr("src.logging_client.monotonic", Mock(side_effect=[0, 1, 61]))
    now = datetime.now(UTC)
    with pytest.raises(TimeoutError):
        LogChecks(settings, client).count(rule, now, now)
    client.list_log_entries.assert_called_once()


def test_cancellation_failure_does_not_mask_query_timeout(settings, rule):
    job = Mock()
    job.result.side_effect = TimeoutError("Original deadline")
    job.cancel.side_effect = RuntimeError("Cannot cancel")
    client = Mock()
    client.query.side_effect = [Mock(statement_type="SELECT", total_bytes_processed=0), job]
    with pytest.raises(TimeoutError, match="Original deadline"):
        BigQueryStore(settings, client).execute_kpi(rule, datetime.now(UTC), datetime.now(UTC))


def test_metadata_null_fields_use_model_defaults(settings):
    client = Mock()
    client.query.return_value.result.return_value = [
        {"rule_id": "one", "location": None, "check_config": '{"table":"p.d.t"}'},
    ]
    row = BigQueryStore(settings, client).read_active("monitoring_rules")[0]
    assert "location" not in row
    assert row["check_config"] == {"table": "p.d.t"}
