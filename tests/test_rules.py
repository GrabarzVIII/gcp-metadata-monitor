from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.checks import build_query
from src.engine import check_rule
from src.models import Rule, Status, evaluate


@pytest.mark.parametrize(
    ("operator", "value", "status"),
    [
        ("LT", 99, Status.ALERT),
        ("LT", 100, Status.OK),
        ("GT", 101, Status.ALERT),
        ("GT", 100, Status.OK),
        ("GE", 100, Status.ALERT),
        ("LE", 100, Status.ALERT),
        ("EQ", 100, Status.ALERT),
        ("NE", 101, Status.ALERT),
        ("OUTSIDE_RANGE", 201, Status.ALERT),
        ("OUTSIDE_RANGE", 200, Status.OK),
        ("OUTSIDE_RANGE", 99, Status.ALERT),
    ],
)
def test_alarm_semantics(rule, operator, value, status):
    rule.comparison_operator = operator
    rule.threshold_upper = 200
    assert evaluate(value, rule)[0] == status


@pytest.mark.parametrize("policy", ["OK", "ALERT", "NO_DATA"])
def test_missing_data_is_explicit(rule, policy):
    rule.no_data_policy = policy
    assert evaluate(None, rule)[0].value == policy


def test_nonfinite_value_is_not_a_success(rule):
    with pytest.raises(ValueError):
        evaluate(float("nan"), rule)


def test_query_failure_is_isolated(rule):
    store = Mock()
    store.execute_kpi.side_effect = RuntimeError("private SQL details")
    result = check_rule(rule, "run", datetime.now(UTC), store, Mock())
    assert result.status == Status.ERROR
    assert "private" not in result.message


def test_unsafe_links_are_rejected(rule):
    data = rule.model_dump()
    data["resource_url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        Rule.model_validate(data)


def test_identifier_injection_is_rejected(rule):
    rule.check_type = "ROW_COUNT"
    rule.check_config = {"table": "p.d.t`; DROP TABLE x", "timestamp_column": "created_at"}
    with pytest.raises(ValueError):
        build_query(rule)


def test_freshness_uses_last_observation_before_window_end(rule):
    rule.check_type = "FRESHNESS"
    rule.check_config = {"table": "p.d.t", "timestamp_column": "loaded_at"}
    query = build_query(rule)
    assert "MAX(`loaded_at`)" in query
    assert "@window_end" in query
    assert "@window_start" not in query


def test_duplicate_check_counts_excess_rows(rule):
    rule.check_type = "DUPLICATES"
    rule.check_config = {
        "table": "p.d.t",
        "timestamp_column": "loaded_at",
        "key_columns": ["id", "date"],
    }
    query = build_query(rule)
    assert "SUM(n - 1)" in query
    assert "GROUP BY `id`, `date`" in query
