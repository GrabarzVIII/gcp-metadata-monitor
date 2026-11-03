"""HTTP Cloud Run function. Cloud Run IAM authenticates requests before execution."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

import functions_framework
from flask import Request, jsonify
from pydantic import AwareDatetime, BaseModel, ConfigDict, StrictBool, ValidationError

from src.bq_client import BigQueryStore
from src.config import get_settings
from src.engine import Monitor
from src.logging_client import LogChecks
from src.notification import EmailSender
from src.state import RunAlreadyClaimed, RunState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: StrictBool = True
    window_end: AwareDatetime | None = None


def create_monitor() -> Monitor:
    settings = get_settings()
    return Monitor(
        settings,
        BigQueryStore(settings),
        LogChecks(settings),
        RunState(settings.state_bucket),
        EmailSender(settings),
    )


@functions_framework.http
def run_monitoring(request: Request):
    """Validate an HTTP request, evaluate rules, and return a JSON run summary."""
    if request.method == "GET" and request.path == "/healthz":
        return jsonify(status="ok"), 200
    if request.path not in {"/", "/run-monitoring"}:
        return jsonify(error="Unknown path"), 404
    if request.method != "POST":
        return jsonify(error="Use POST with a JSON body"), 405, {"Allow": "POST"}
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object"), 400
    schedule_time = request.headers.get("X-CloudScheduler-ScheduleTime")
    job_name = request.headers.get("X-CloudScheduler-JobName")
    try:
        body = RunRequest.model_validate(payload)
        if bool(schedule_time) != bool(job_name):
            raise ValueError("Scheduler headers must be supplied together")
        scheduled = None
        if schedule_time:
            scheduled = datetime.fromisoformat(schedule_time.replace("Z", "+00:00"))
            if scheduled.tzinfo is None:
                raise ValueError("Scheduler timestamp must include a timezone")
        if scheduled and body.window_end is not None:
            raise ValueError("Scheduled requests cannot override their observation window")
    except (ValidationError, ValueError):
        return jsonify(error="Invalid request fields or Scheduler timestamp"), 400
    end = (body.window_end or scheduled or datetime.now(UTC)).astimezone(UTC)
    key = f"{job_name}:{end.isoformat()}:{body.preview}" if scheduled else str(uuid4())
    try:
        result = create_monitor().run(key, end, preview=body.preview)
    except RunAlreadyClaimed:
        return jsonify(error="Run already claimed; inspect its stored state"), 409
    except Exception as exc:
        logger.error("monitor_run_failed error_type=%s", type(exc).__name__)
        return jsonify(error="Monitoring failed; inspect logs and run state"), 500
    return jsonify(result), 200 if result["status"] == "COMPLETED" else 500
