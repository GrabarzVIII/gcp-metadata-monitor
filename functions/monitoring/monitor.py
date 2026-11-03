"""SQL and log checks, durable run state, and monitoring orchestration."""

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from numbers import Real
from time import monotonic
from typing import Any

from google.api_core.exceptions import PreconditionFailed
from google.cloud import bigquery, storage
from google.cloud.logging_v2.services.logging_service_v2 import LoggingServiceV2Client

from config import Profile, Result, Rule, Settings, Status, evaluate
from report import render_report

logger = logging.getLogger(__name__)


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
        self.checkpoint(run_id, record)

    def checkpoint(self, run_id: str, record: dict) -> None:
        """Keep the latest delivery evidence on the exclusively owned run claim."""
        blob = self.bucket.blob(f"runs/{run_id}.json")
        blob.upload_from_string(json.dumps(record), content_type="application/json", timeout=30)

    def save_report(self, run_id: str, profile_id: str, html: str) -> None:
        self.bucket.blob(f"reports/{run_id}/{profile_id}.html").upload_from_string(
            html,
            content_type="text/html; charset=utf-8",
            timeout=30,
        )


def execution_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def check_rule(rule: Rule, run_id: str, end: datetime, store: Any, logs: Any) -> Result:
    start = end - timedelta(minutes=rule.window_minutes)
    started = time.monotonic()
    value = None
    try:
        value = (
            logs.count(rule, start, end)
            if rule.check_type == "LOG"
            else (store.execute_kpi(rule, start, end))
        )
        status, message = evaluate(value, rule)
    except Exception as exc:
        # Do not expose SQL, credentials, log payloads, or API response bodies in reports.
        logger.error("check_failed rule_id=%s error_type=%s", rule.rule_id, type(exc).__name__)
        status, message = (
            Status.ERROR,
            f"Check failed ({type(exc).__name__}); inspect configuration.",
        )
        value = None
    return Result(
        run_id=run_id,
        rule_id=rule.rule_id,
        rule_name=rule.rule_name,
        rule_version=rule.rule_version,
        project_id=rule.project_id,
        service_type=rule.service_type,
        resource_name=rule.resource_name,
        resource_url=rule.resource_url,
        check_type=rule.check_type,
        status=status,
        value=value,
        comparison_operator=rule.comparison_operator,
        threshold_value=rule.threshold_value,
        threshold_upper=rule.threshold_upper,
        severity=rule.severity,
        window_start=start,
        window_end=end,
        checked_at=datetime.now(UTC),
        duration_ms=int((time.monotonic() - started) * 1000),
        message=message,
        target_profiles=rule.target_profiles,
        owner=rule.owner,
        pipeline_id=rule.pipeline_id,
        runbook_url=rule.runbook_url,
        rule_snapshot=rule.model_dump_json(),
    )


class Monitor:
    def __init__(self, settings: Settings, store: Any, logs: Any, state: Any, sender: Any):
        self.settings, self.store, self.logs = settings, store, logs
        self.state, self.sender = state, sender

    def run(self, key: str, end: datetime, *, preview: bool = True) -> dict:
        run_id = execution_id(key)
        existing = self.state.claim(run_id)
        if existing:
            return existing
        started = datetime.now(UTC)
        deadline = time.monotonic() + self.settings.run_budget_seconds
        record = {
            "run_id": run_id,
            "started_at": started.isoformat(),
            "window_end": end.isoformat(),
            "finished_at": None,
            "status": "RUNNING",
            "rule_count": 0,
            "problem_count": 0,
            "configuration_errors": [],
            "notifications": [],
            "preview": preview,
        }
        results: list[Result] = []
        results_write_attempted = False
        failure: Exception | None = None
        try:
            profiles = self._profiles()
            rows = self.store.read_active("monitoring_rules")
            ids = [row.get("rule_id") for row in rows]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate rule_id values in metadata")
            for raw in rows:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Run budget exceeded; split rules into smaller deployments")
                try:
                    rule = Rule.model_validate(raw)
                    if set(rule.target_profiles) - profiles.keys():
                        raise ValueError("Unknown or inactive notification profile")
                except Exception:
                    rule_id = str(raw.get("rule_id", "unknown"))
                    record["configuration_errors"].append(rule_id)
                    logger.error("invalid_rule rule_id=%s", rule_id)
                    continue
                result = check_rule(rule, run_id, end, self.store, self.logs)
                results.append(result)
                record["rule_count"] += 1
                record["problem_count"] += result.status != Status.OK
            # One load job per run avoids per-table load quotas at higher rule counts.
            results_write_attempted = True
            self.store.save_results(results)
            for profile in profiles.values():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Run budget exceeded before report delivery")
                html = render_report(profile, results, self.settings.report_timezone)
                if html is None:
                    continue
                delivery = {
                    "profile_id": profile.profile_id,
                    "status": "PREVIEW" if preview else "DISABLED",
                }
                try:
                    self.state.save_report(run_id, profile.profile_id, html)
                    if not preview and self.settings.notifications_enabled:
                        self.sender.send(profile, html, run_id)
                        delivery["status"] = "SENT"
                except Exception as exc:
                    delivery["status"] = "FAILED"
                    logger.error(
                        "notification_failed profile_id=%s error_type=%s",
                        profile.profile_id,
                        type(exc).__name__,
                    )
                record["notifications"].append(delivery)
                # Persist accepted deliveries before attempting the next profile.
                self.state.checkpoint(run_id, record)
            record["status"] = "COMPLETED"
            if (
                not results
                or record["configuration_errors"]
                or any(result.status == Status.ERROR for result in results)
                or any(item["status"] == "FAILED" for item in record["notifications"])
            ):
                record["status"] = "NEEDS_ATTENTION"
        except Exception as exc:
            failure = exc
            record["status"] = "FAILED"

        # Preserve partial observations, but never retry an ambiguous BigQuery load here.
        if results and not results_write_attempted:
            try:
                self.store.save_results(results)
            except Exception as exc:
                failure = failure or exc
                record["status"] = "FAILED"
        record["finished_at"] = datetime.now(UTC).isoformat()
        try:
            self.store.save_run(
                {
                    **record,
                    "notifications": json.dumps(record["notifications"]),
                }
            )
        except Exception as exc:
            failure = failure or exc
            record["status"] = "FAILED"
            logger.error("run_history_failed run_id=%s error_type=%s", run_id, type(exc).__name__)
        try:
            # Save a terminal state even when BigQuery history is unavailable.
            self.state.finish(run_id, record)
        except Exception as exc:
            failure = failure or exc
            record["status"] = "FAILED"
            logger.error("run_state_failed run_id=%s error_type=%s", run_id, type(exc).__name__)
        logger.info("monitor_run_finished run_id=%s status=%s", run_id, record["status"])
        if failure is not None:
            raise failure
        return record

    def _profiles(self) -> dict[str, Profile]:
        profiles = [
            Profile.model_validate(row)
            for row in self.store.read_active(
                "notification_profiles",
            )
        ]
        if len({profile.profile_id for profile in profiles}) != len(profiles):
            raise ValueError("Duplicate profile_id values in metadata")
        return {profile.profile_id: profile for profile in profiles}
