from datetime import UTC, datetime
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
    client.list_entries.return_value = [1, 2, 3]
    with pytest.raises(ValueError, match="limit"):
        LogChecks(settings, client).count(rule, datetime.now(UTC), datetime.now(UTC))


def test_log_filter_is_scoped_to_project_and_window(settings, rule):
    rule.log_filter = 'severity="ERROR" OR severity="CRITICAL"'
    client = Mock()
    client.list_entries.return_value = []
    now = datetime.now(UTC)
    assert LogChecks(settings, client).count(rule, now, now) == 0
    arguments = client.list_entries.call_args.kwargs
    assert arguments["resource_names"] == ["projects/source-project"]
    assert arguments["filter_"].startswith(f"({rule.log_filter}) AND")
