"""Atomic run claims in Cloud Storage prevent overlapping duplicate executions."""

import json
from datetime import UTC, datetime
from typing import Any

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage


class RunAlreadyClaimed(Exception):
    """The scheduled execution was already claimed; inspect its stored state."""


class RunState:
    def __init__(self, bucket: str, client: Any = None):
        if not bucket:
            raise ValueError("STATE_BUCKET must be configured")
        self.bucket = (client or storage.Client()).bucket(bucket)

    def claim(self, run_id: str) -> dict | None:
        blob = self.bucket.blob(f"runs/{run_id}.json")
        try:
            blob.upload_from_string(
                json.dumps({"status": "RUNNING", "started_at": datetime.now(UTC).isoformat()}),
                content_type="application/json",
                if_generation_match=0,
                timeout=30,
            )
        except PreconditionFailed:
            existing = json.loads(blob.download_as_text(timeout=30))
            if existing["status"] == "COMPLETED":
                return existing
            raise RunAlreadyClaimed(run_id) from None
        return None

    def finish(self, run_id: str, record: dict) -> None:
        blob = self.bucket.blob(f"runs/{run_id}.json")
        blob.upload_from_string(json.dumps(record), content_type="application/json", timeout=30)

    def save_report(self, run_id: str, profile_id: str, html: str) -> None:
        self.bucket.blob(f"reports/{run_id}/{profile_id}.html").upload_from_string(
            html,
            content_type="text/html; charset=utf-8",
            timeout=30,
        )
