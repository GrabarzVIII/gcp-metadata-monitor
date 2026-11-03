"""Default environment settings and their conversion to a plain dictionary."""

import os
import re
from zoneinfo import ZoneInfo

DEFAULTS = {
    "GOOGLE_CLOUD_PROJECT": "",
    "METADATA_DATASET": "monitoring",
    "BQ_LOCATION": "EU",
    "STATE_BUCKET": "",
    "REPORT_TIMEZONE": "Europe/Warsaw",
    "MAXIMUM_BYTES_BILLED": 1_073_741_824,
    "QUERY_TIMEOUT_SECONDS": 60,
    "LOG_ENTRY_LIMIT": 10_000,
    "RUN_BUDGET_SECONDS": 1500,
    "SMTP_HOST": "",
    "SMTP_PORT": 587,
    "SMTP_USER": "",
    "SMTP_PASS": "",
    "SMTP_FROM": "",
    "SMTP_SECURITY": "starttls",
    "NOTIFICATIONS_ENABLED": False,
}


def get_settings(environment=None) -> dict:
    """Environment variables override DEFAULTS; their types follow the defaults."""
    environment = os.environ if environment is None else environment
    settings = {}
    for name, default in DEFAULTS.items():
        value = environment.get(name, default)
        if isinstance(default, bool):
            value = str(value).lower()
            if value not in ("true", "false", "1", "0", "yes", "no", "on", "off", "t", "f"):
                raise ValueError(f"{name} must be a boolean")
            value = value in ("true", "1", "yes", "on", "t")
        elif isinstance(default, int):
            value = int(value)
            if value < 1:
                raise ValueError(f"{name} must be a positive integer")
        elif not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        settings[name.lower()] = value

    for name, maximum in (
        ("query_timeout_seconds", 300),
        ("run_budget_seconds", 1500),
        ("smtp_port", 65535),
    ):
        if settings[name] > maximum:
            raise ValueError(f"{name} exceeds {maximum}")
    for name, pattern in (
        ("google_cloud_project", r"[a-z][a-z0-9-]{4,61}[a-z0-9]"),
        ("metadata_dataset", r"[A-Za-z_]\w*"),
    ):
        if not re.fullmatch(pattern, settings[name]):
            raise ValueError(f"Invalid {name}")
    if settings["smtp_security"] not in ("starttls", "ssl"):
        raise ValueError("SMTP_SECURITY must be starttls or ssl")
    ZoneInfo(settings["report_timezone"])
    settings["dataset_id"] = f"{settings['google_cloud_project']}.{settings['metadata_dataset']}"
    return settings
