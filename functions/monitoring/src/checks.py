"""Build safe, parameterized queries for common data quality checks."""

import re

from src.models import Rule


def identifier(value: str, *, table: bool = False) -> str:
    pattern = r"[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+" if table else r"[A-Za-z_]\w*"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError("Invalid table or column identifier")
    return f"`{value}`"


def build_query(rule: Rule) -> str:
    """Return a scalar query; all template timestamp columns must be TIMESTAMP."""
    if rule.check_type == "SQL_KPI":
        return rule.kpi_query or ""
    config = rule.check_config
    table = identifier(config["table"], table=True)
    timestamp = identifier(config["timestamp_column"])
    window = f"{timestamp} >= @window_start AND {timestamp} < @window_end"
    if rule.check_type == "FRESHNESS":
        return (
            f"SELECT TIMESTAMP_DIFF(@window_end, MAX({timestamp}), SECOND) / 60.0 AS value "
            f"FROM {table} WHERE {timestamp} < @window_end"
        )
    if rule.check_type == "ROW_COUNT":
        return f"SELECT COUNT(*) AS value FROM {table} WHERE {window}"
    if rule.check_type == "NULL_RATE":
        column = identifier(config["column"])
        return (
            f"SELECT SAFE_DIVIDE(COUNTIF({column} IS NULL), COUNT(*)) AS value "
            f"FROM {table} WHERE {window}"
        )
    if rule.check_type == "DUPLICATES":
        keys = config["key_columns"]
        if not isinstance(keys, list) or not keys:
            raise ValueError("DUPLICATES requires a non-empty key_columns list")
        columns = ", ".join(identifier(key) for key in keys)
        return (
            "SELECT COALESCE(SUM(n - 1), 0) AS value FROM "
            f"(SELECT COUNT(*) AS n FROM {table} WHERE {window} GROUP BY {columns})"
        )
    raise ValueError(f"Unsupported SQL check type: {rule.check_type}")
