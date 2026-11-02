"""Evaluate rules independently, persist evidence, and route reports by profile."""

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import Settings
from src.models import Profile, Result, Rule, Status, evaluate
from src.report_generator import render_report

logger = logging.getLogger(__name__)


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
        try:
            profiles = self._profiles()
            rows = self.store.read_active("monitoring_rules")
            ids = [row.get("rule_id") for row in rows]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate rule_id values in metadata")
            results = []
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
                self.store.save_results([result])
                results.append(result)
            record["rule_count"] = len(results)
            record["problem_count"] = sum(result.status != Status.OK for result in results)
            for profile in profiles.values():
                html = render_report(profile, results, self.settings.report_timezone)
                if html is None:
                    continue
                self.state.save_report(run_id, profile.profile_id, html)
                delivery = {"profile_id": profile.profile_id, "status": "PREVIEW"}
                if not preview and self.settings.notifications_enabled:
                    try:
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
            record["status"] = "COMPLETED"
            if record["configuration_errors"] or any(
                item["status"] == "FAILED" for item in record["notifications"]
            ):
                record["status"] = "NEEDS_ATTENTION"
            return record
        except Exception:
            record["status"] = "FAILED"
            raise
        finally:
            record["finished_at"] = datetime.now(UTC).isoformat()
            # Keep the claim on failure. Blind retries could duplicate partially delivered emails.
            self.store.save_run(
                {
                    **record,
                    "notifications": json.dumps(record["notifications"]),
                }
            )
            self.state.finish(run_id, record)
            logger.info("monitor_run_finished run_id=%s status=%s", run_id, record["status"])

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
