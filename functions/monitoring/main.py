"""HTTP Cloud Run function. Cloud Run IAM authenticates requests before execution."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

import functions_framework
from config import get_settings, require_boolean
from flask import Request, jsonify
from monitor import create_clients, run_monitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO 8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


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
        if payload.keys() - {"preview", "window_end"}:
            raise ValueError("Unexpected request fields")
        preview = payload.get("preview", True)
        require_boolean(preview, "preview")
        window_end = payload.get("window_end")
        if window_end is not None:
            window_end = parse_timestamp(window_end)
        if bool(schedule_time) != bool(job_name):
            raise ValueError("Scheduler headers must be supplied together")
        scheduled = parse_timestamp(schedule_time) if schedule_time else None
        if scheduled and window_end is not None:
            raise ValueError("Scheduled requests cannot override their observation window")
    except ValueError:
        return jsonify(error="Invalid request fields or Scheduler timestamp"), 400
    end = (window_end or scheduled or datetime.now(UTC)).astimezone(UTC)
    key = f"{job_name}:{end.isoformat()}:{preview}" if scheduled else str(uuid4())
    try:
        settings = get_settings()
        result = run_monitor(settings, create_clients(settings), key, end, preview=preview)
    except Exception as exc:
        logger.error("monitor_run_failed error_type=%s", type(exc).__name__)
        return jsonify(error="Monitoring failed; inspect logs and run state"), 500
    if result is None:
        return jsonify(error="Run already claimed; inspect its stored state"), 409
    return jsonify(result), 200 if result["status"] == "COMPLETED" else 500
