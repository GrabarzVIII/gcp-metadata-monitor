"""Count matching Cloud Logging entries in a fixed half-open time window."""

from datetime import datetime
from typing import Any

from google.cloud import logging_v2

from src.config import Settings
from src.models import Rule


class LogChecks:
    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        self.client = client or logging_v2.Client(project=settings.google_cloud_project)

    def count(self, rule: Rule, start: datetime, end: datetime) -> float:
        query = (
            f'({rule.log_filter}) AND timestamp >= "{start.isoformat()}" '
            f'AND timestamp < "{end.isoformat()}"'
        )
        entries = self.client.list_entries(
            resource_names=[f"projects/{rule.project_id}"],
            filter_=query,
            order_by=logging_v2.ASCENDING,
            page_size=1000,
            max_results=self.settings.log_entry_limit + 1,
        )
        count = sum(1 for _ in entries)
        if count > self.settings.log_entry_limit:
            raise ValueError("Log scan limit exceeded; narrow the filter or time window")
        return float(count)
