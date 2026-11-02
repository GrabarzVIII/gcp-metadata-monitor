"""Count matching Cloud Logging entries in a fixed half-open time window."""

from datetime import datetime
from time import monotonic
from typing import Any

from google.cloud.logging_v2.services.logging_service_v2 import LoggingServiceV2Client

from src.config import Settings
from src.models import Rule


class LogChecks:
    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        self.client = client or LoggingServiceV2Client()

    def count(self, rule: Rule, start: datetime, end: datetime) -> float:
        query = (
            f'({rule.log_filter}) AND timestamp >= "{start.isoformat()}" '
            f'AND timestamp < "{end.isoformat()}"'
        )
        deadline = monotonic() + self.settings.query_timeout_seconds
        count = 0
        page_token = ""
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Log scan exceeded its deadline")
            page = self.client.list_log_entries(
                request={
                    "resource_names": [f"projects/{rule.project_id}"],
                    "filter": query,
                    "order_by": "timestamp asc",
                    "page_size": min(1000, self.settings.log_entry_limit + 1 - count),
                    "page_token": page_token,
                },
                timeout=remaining,
                retry=None,
            )
            # Read one page explicitly so every RPC shares the same deadline.
            count += len(page.entries)
            if count > self.settings.log_entry_limit:
                raise ValueError("Log scan limit exceeded; narrow the filter or time window")
            page_token = page.next_page_token
            if not page_token:
                return float(count)
