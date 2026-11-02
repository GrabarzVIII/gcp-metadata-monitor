"""Validated rule definitions and durable result records."""

import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Status(StrEnum):
    OK = "OK"
    ALERT = "ALERT"
    ERROR = "ERROR"
    NO_DATA = "NO_DATA"


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    rule_name: str = Field(min_length=1)
    rule_version: int = Field(default=1, ge=1)
    project_id: str = Field(pattern=r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
    service_type: Literal["CLOUD_RUN", "SCHEDULER", "WORKFLOWS", "DATAFORM", "BIGQUERY"]
    resource_name: str
    resource_id: str = ""
    resource_url: str = ""
    location: str = "EU"
    check_type: Literal["LOG", "SQL_KPI", "FRESHNESS", "ROW_COUNT", "NULL_RATE", "DUPLICATES"]
    log_filter: str | None = None
    kpi_query: str | None = None
    check_config: dict[str, Any] = Field(default_factory=dict)
    comparison_operator: Literal["GT", "GE", "LT", "LE", "EQ", "NE", "OUTSIDE_RANGE"]
    threshold_value: float = Field(allow_inf_nan=False)
    threshold_upper: float | None = Field(default=None, allow_inf_nan=False)
    window_minutes: int = Field(default=1440, ge=1)
    severity: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING"
    no_data_policy: Literal["ALERT", "NO_DATA", "OK"] = "NO_DATA"
    target_profiles: list[str] = Field(default_factory=list)
    pipeline_id: str = ""
    owner: str = ""
    runbook_url: str = ""
    is_active: bool = True

    @field_validator("resource_url", "runbook_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        if value and (urlparse(value).scheme != "https" or not urlparse(value).netloc):
            raise ValueError("Links must be absolute HTTPS URLs")
        return value

    @model_validator(mode="after")
    def validate_check(self) -> "Rule":
        if self.check_type == "LOG" and not (self.log_filter or "").strip():
            raise ValueError("LOG checks require a resource-scoped log_filter")
        if self.check_type == "SQL_KPI" and not (self.kpi_query or "").strip():
            raise ValueError("SQL_KPI checks require kpi_query")
        if self.comparison_operator == "OUTSIDE_RANGE":
            if self.threshold_upper is None or self.threshold_upper < self.threshold_value:
                raise ValueError("OUTSIDE_RANGE requires threshold_upper >= threshold_value")
        return self


class Profile(BaseModel):
    profile_id: str
    email_addresses: list[str] = Field(min_length=1)
    only_problems: bool = False
    is_active: bool = True

    @field_validator("email_addresses")
    @classmethod
    def validate_addresses(cls, values: list[str]) -> list[str]:
        for value in values:
            if "@" not in value or any(c in value for c in "\r\n,;"):
                raise ValueError("Each recipient must be a single email address")
        return values


class Result(BaseModel):
    run_id: str
    rule_id: str
    rule_name: str
    rule_version: int
    project_id: str
    service_type: str
    resource_name: str
    resource_url: str
    check_type: str
    status: Status
    value: float | None = None
    comparison_operator: str
    threshold_value: float
    threshold_upper: float | None = None
    severity: str
    window_start: datetime
    window_end: datetime
    checked_at: datetime
    duration_ms: int
    message: str
    target_profiles: list[str]
    owner: str
    pipeline_id: str
    runbook_url: str
    rule_snapshot: str


def evaluate(value: float | None, rule: Rule) -> tuple[Status, str]:
    """Interpret thresholds as alarm conditions, never as success conditions."""
    if value is None:
        return Status(rule.no_data_policy), "No data returned; applied no_data_policy."
    if not math.isfinite(value):
        raise ValueError("KPI returned a non-finite value")
    threshold = rule.threshold_value
    alarm = {
        "GT": lambda: value > threshold,
        "GE": lambda: value >= threshold,
        "LT": lambda: value < threshold,
        "LE": lambda: value <= threshold,
        "EQ": lambda: value == threshold,
        "NE": lambda: value != threshold,
        "OUTSIDE_RANGE": lambda: value < threshold or value > rule.threshold_upper,
    }[rule.comparison_operator]()
    return (Status.ALERT, "Alarm condition met.") if alarm else (Status.OK, "Check passed.")
