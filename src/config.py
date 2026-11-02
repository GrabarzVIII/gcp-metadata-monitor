"""Environment settings; credentials use Application Default Credentials."""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
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
