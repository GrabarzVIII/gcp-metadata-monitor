"""BigQuery metadata storage and bounded, read-only scalar queries."""

import json
import logging
from datetime import datetime
from decimal import Decimal
from numbers import Real
from typing import Any

from google.cloud import bigquery

from src.checks import build_query
from src.config import Settings
from src.models import Result, Rule

logger = logging.getLogger(__name__)


class BigQueryStore:
    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        self.client = client or bigquery.Client(project=settings.google_cloud_project)

    def read_active(self, table: str) -> list[dict]:
        if table not in {"monitoring_rules", "notification_profiles"}:
            raise ValueError("Unsupported metadata table")
        job = self.client.query(
            f"SELECT * FROM `{self.settings.dataset_id}.{table}` WHERE is_active = TRUE",
            location=self.settings.bq_location,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=self.settings.maximum_bytes_billed,
            ),
            timeout=30,
        )
        rows = [dict(row) for row in self._result(job)]
        for row in rows:
            if isinstance(row.get("check_config"), str):
                row["check_config"] = json.loads(row["check_config"])
        # Nullable metadata columns use model defaults when omitted by an INSERT.
        return [{key: value for key, value in row.items() if value is not None} for row in rows]

    def execute_kpi(self, rule: Rule, start: datetime, end: datetime) -> float | None:
        sql = build_query(rule)
        parameters = [
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", start),
            bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", end),
        ]
        config = bigquery.QueryJobConfig(
            query_parameters=parameters,
            use_legacy_sql=False,
            maximum_bytes_billed=self.settings.maximum_bytes_billed,
            dry_run=True,
        )
        # BigQuery parses the SQL; prefix checks alone cannot reject scripts or DML safely.
        estimate = self.client.query(sql, job_config=config, location=rule.location, timeout=30)
        if estimate.statement_type != "SELECT":
            raise ValueError("KPI queries must be a single SELECT statement")
        if (estimate.total_bytes_processed or 0) > self.settings.maximum_bytes_billed:
            raise ValueError("KPI query exceeds the configured scan budget")
        config.dry_run = False
        config.labels = {"application": "gcp-metadata-monitor"}
        job = self.client.query(sql, job_config=config, location=rule.location, timeout=30)
        rows = self._result(job, max_results=2)
        if len(rows.schema) != 1 or rows.schema[0].name != "value":
            raise ValueError("KPI query must return exactly one column named value")
        values = list(rows)
        if len(values) != 1:
            raise ValueError("KPI query must return exactly one row (use NULL for no data)")
        value = values[0][0]
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
            raise ValueError("KPI value must be numeric")
        return float(value)

    def _result(self, job: Any, **kwargs: Any) -> Any:
        try:
            return job.result(timeout=self.settings.query_timeout_seconds, **kwargs)
        except TimeoutError:
            try:
                job.cancel(timeout=10)
            except Exception as exc:
                logger.warning("job_cancel_failed error_type=%s", type(exc).__name__)
            raise

    def save_results(self, results: list[Result]) -> None:
        if results:
            self._append("monitoring_results", [r.model_dump(mode="json") for r in results])

    def save_run(self, record: dict) -> None:
        self._append("monitoring_runs", [record])

    def _append(self, table: str, records: list[dict]) -> None:
        # Load jobs avoid the best-effort deduplication of streaming inserts.
        job = self.client.load_table_from_json(
            records,
            f"{self.settings.dataset_id}.{table}",
            location=self.settings.bq_location,
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
            job_id=f"monitor_{table}_{records[0]['run_id']}",
            timeout=30,
        )
        self._result(job)
