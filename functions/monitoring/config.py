"""Read environment settings and validate metadata as plain dictionaries."""

import math
import os
import re
from decimal import Decimal
from numbers import Real
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

PROJECT_PATTERN = r"[a-z][a-z0-9-]{4,61}[a-z0-9]"
ID_PATTERN = r"[A-Za-z0-9_-]{1,128}"


def require_string(value, name: str, pattern: str | None = None) -> None:
    if not isinstance(value, str) or (pattern and not re.fullmatch(pattern, value)):
        raise ValueError(f"Invalid {name}")


def require_integer(value, name: str, maximum: int | None = None) -> None:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {name}")


def require_boolean(value, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def require_choice(value, name: str, choices: tuple) -> None:
    if value not in choices:
        raise ValueError(f"Invalid {name}")


def require_string_list(value, name: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")


def finite_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def with_defaults(data: dict, defaults: dict, required: tuple) -> dict:
    """Reject misspelled and missing fields before applying per-call defaults."""
    if not isinstance(data, dict) or set(data) - (defaults.keys() | set(required)):
        raise ValueError("Unexpected metadata fields")
    if set(required) - data.keys():
        raise ValueError("Missing required metadata fields")
    return {**defaults, **data}


def get_settings(environment=None) -> dict:
    """Read the environment configured by Cloud Run or the local shell."""
    environment = os.environ if environment is None else environment
    defaults = {
        "google_cloud_project": "",
        "metadata_dataset": "monitoring",
        "bq_location": "EU",
        "state_bucket": "",
        "report_timezone": "Europe/Warsaw",
        "smtp_host": "",
        "smtp_user": "",
        "smtp_pass": "",
        "smtp_from": "",
        "smtp_security": "starttls",
    }
    settings = {key: environment.get(key.upper(), default) for key, default in defaults.items()}
    for key, value in settings.items():
        require_string(value, key)
    for key, default, maximum in (
        ("maximum_bytes_billed", 1_073_741_824, None),
        ("query_timeout_seconds", 60, 300),
        ("log_entry_limit", 10_000, None),
        ("run_budget_seconds", 1500, 1500),
        ("smtp_port", 587, 65535),
    ):
        value = int(environment.get(key.upper(), str(default)))
        require_integer(value, key, maximum)
        settings[key] = value
    enabled = environment.get("NOTIFICATIONS_ENABLED", "false").lower()
    require_choice(
        enabled,
        "notifications_enabled",
        ("true", "false", "1", "0", "yes", "no", "on", "off", "t", "f"),
    )
    settings["notifications_enabled"] = enabled in {"true", "1", "yes", "on", "t"}
    require_string(settings["google_cloud_project"], "google_cloud_project", PROJECT_PATTERN)
    require_string(settings["metadata_dataset"], "metadata_dataset", r"[A-Za-z_]\w*")
    require_choice(settings["smtp_security"], "smtp_security", ("starttls", "ssl"))
    ZoneInfo(settings["report_timezone"])
    settings["dataset_id"] = f"{settings['google_cloud_project']}.{settings['metadata_dataset']}"
    return settings


def validate_rule(data: dict) -> dict:
    rule = with_defaults(
        data,
        {
            "rule_version": 1,
            "resource_id": "",
            "resource_url": "",
            "location": "EU",
            "log_filter": None,
            "kpi_query": None,
            "check_config": {},
            "threshold_upper": None,
            "window_minutes": 1440,
            "severity": "WARNING",
            "no_data_policy": "NO_DATA",
            "target_profiles": [],
            "pipeline_id": "",
            "owner": "",
            "runbook_url": "",
            "is_active": True,
        },
        (
            "rule_id",
            "rule_name",
            "project_id",
            "service_type",
            "resource_name",
            "check_type",
            "comparison_operator",
            "threshold_value",
        ),
    )
    require_string(rule["rule_id"], "rule_id", ID_PATTERN)
    require_string(rule["project_id"], "project_id", PROJECT_PATTERN)
    for key in (
        "rule_name",
        "resource_name",
        "resource_id",
        "resource_url",
        "location",
        "pipeline_id",
        "owner",
        "runbook_url",
    ):
        require_string(rule[key], key)
    if not rule["rule_name"]:
        raise ValueError("rule_name must not be empty")
    for key in ("rule_version", "window_minutes"):
        require_integer(rule[key], key)
    require_boolean(rule["is_active"], "is_active")
    require_string_list(rule["target_profiles"], "target_profiles")
    for key, choices in (
        ("service_type", ("CLOUD_RUN", "SCHEDULER", "WORKFLOWS", "DATAFORM", "BIGQUERY")),
        ("check_type", ("LOG", "SQL_KPI", "FRESHNESS", "ROW_COUNT", "NULL_RATE", "DUPLICATES")),
        ("comparison_operator", ("GT", "GE", "LT", "LE", "EQ", "NE", "OUTSIDE_RANGE")),
        ("severity", ("INFO", "WARNING", "CRITICAL")),
        ("no_data_policy", ("ALERT", "NO_DATA", "OK")),
    ):
        require_choice(rule[key], key, choices)
    if not isinstance(rule["check_config"], dict):
        raise ValueError("check_config must be an object")
    for key in ("log_filter", "kpi_query"):
        if rule[key] is not None:
            require_string(rule[key], key)
    for key in ("resource_url", "runbook_url"):
        url = urlparse(rule[key])
        if rule[key] and (url.scheme != "https" or not url.netloc):
            raise ValueError("Links must be absolute HTTPS URLs")
    rule["threshold_value"] = finite_number(rule["threshold_value"], "threshold_value")
    if rule["threshold_upper"] is not None:
        rule["threshold_upper"] = finite_number(rule["threshold_upper"], "threshold_upper")
    if rule["check_type"] == "LOG" and not (rule["log_filter"] or "").strip():
        raise ValueError("LOG checks require a resource-scoped log_filter")
    if rule["check_type"] == "SQL_KPI" and not (rule["kpi_query"] or "").strip():
        raise ValueError("SQL_KPI checks require kpi_query")
    if rule["comparison_operator"] == "OUTSIDE_RANGE" and (
        rule["threshold_upper"] is None or rule["threshold_upper"] < rule["threshold_value"]
    ):
        raise ValueError("OUTSIDE_RANGE requires threshold_upper >= threshold_value")
    return rule


def validate_profile(data: dict) -> dict:
    profile = with_defaults(
        data, {"only_problems": False, "is_active": True}, ("profile_id", "email_addresses")
    )
    require_string(profile["profile_id"], "profile_id", ID_PATTERN)
    require_string_list(profile["email_addresses"], "email_addresses")
    if not profile["email_addresses"]:
        raise ValueError("At least one email address is required")
    for address in profile["email_addresses"]:
        if "@" not in address or any(c in address for c in "\r\n,;"):
            raise ValueError("Each recipient must be a single email address")
    for key in ("only_problems", "is_active"):
        require_boolean(profile[key], key)
    return profile


def evaluate(value: float | None, rule: dict) -> tuple[str, str]:
    """Interpret thresholds as alarm conditions, never as success conditions."""
    if value is None:
        return rule["no_data_policy"], "No data returned; applied no_data_policy."
    if not math.isfinite(value):
        raise ValueError("KPI returned a non-finite value")
    threshold = rule["threshold_value"]
    alarm = {
        "GT": lambda: value > threshold,
        "GE": lambda: value >= threshold,
        "LT": lambda: value < threshold,
        "LE": lambda: value <= threshold,
        "EQ": lambda: value == threshold,
        "NE": lambda: value != threshold,
        "OUTSIDE_RANGE": lambda: value < threshold or value > rule["threshold_upper"],
    }[rule["comparison_operator"]]()
    return ("ALERT", "Alarm condition met.") if alarm else ("OK", "Check passed.")
