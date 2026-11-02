"""Private Cloud Run HTTP entrypoint. Authentication is enforced by Cloud Run IAM."""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import AwareDatetime, BaseModel

from src.bq_client import BigQueryStore
from src.config import get_settings
from src.engine import Monitor
from src.logging_client import LogChecks
from src.notification import EmailSender
from src.state import RunAlreadyClaimed, RunState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI(title="GCP Metadata Monitor", docs_url=None, redoc_url=None)


class RunRequest(BaseModel):
    preview: bool = True
    window_end: AwareDatetime | None = None


@app.get("/healthz")
def health() -> dict:
    """Return process liveness without initiating any cloud API calls."""
    return {"status": "ok"}


@app.post("/run-monitoring")
def run_monitoring(
    request: RunRequest,
    schedule_time: Annotated[str | None, Header(alias="X-CloudScheduler-ScheduleTime")] = None,
    job_name: Annotated[str | None, Header(alias="X-CloudScheduler-JobName")] = None,
) -> dict:
    settings = get_settings()
    try:
        scheduled = (
            datetime.fromisoformat(schedule_time.replace("Z", "+00:00"))
            if (schedule_time)
            else None
        )
        if scheduled and scheduled.tzinfo is None:
            raise ValueError("Missing timezone")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid schedule timestamp") from exc
    end = request.window_end or scheduled or datetime.now(UTC)
    key = (
        f"{job_name}:{schedule_time}:{request.preview}" if scheduled and job_name else str(uuid4())
    )
    monitor = Monitor(
        settings,
        BigQueryStore(settings),
        LogChecks(settings),
        RunState(settings.state_bucket),
        EmailSender(settings),
    )
    try:
        result = monitor.run(key, end.astimezone(UTC), preview=request.preview)
    except RunAlreadyClaimed as exc:
        raise HTTPException(
            status_code=409, detail="Run already claimed; inspect stored state"
        ) from exc
    except Exception as exc:
        logger.error("monitor_run_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Monitoring failed; inspect run state") from exc
    if result["status"] != "COMPLETED":
        raise HTTPException(status_code=500, detail=result)
    return result
