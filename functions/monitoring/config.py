"""Environment settings, rule validation, and result definitions."""

import math
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_cloud_project: str = Field(pattern=r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
    metadata_dataset: str = Field(default="monitoring", pattern=r"^[A-Za-z_]\w*$")
    bq_location: str = "EU"
    maximum_bytes_billed: int = Field(default=1_073_741_824, gt=0)
    query_timeout_seconds: int = Field(default=60, ge=1, le=300)
    log_entry_limit: int = Field(default=10_000, ge=1)
    run_budget_seconds: int = Field(default=1500, ge=1, le=1500)
    state_bucket: str = ""
    report_timezone: str = "Europe/Warsaw"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_pass: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    notifications_enabled: bool = False

    @field_validator("report_timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def dataset_id(self) -> str:
        return f"{self.google_cloud_project}.{self.metadata_dataset}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


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
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
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
