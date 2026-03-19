"""Agent status and trigger REST API endpoints."""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from agent.scheduler import get_manual_run_status, start_manual_run
from database import get_db
from models import AgentRun

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Will be set by main.py after config is loaded
_config: dict = {}


def set_config(config: dict) -> None:
    """Store config reference for manual run triggers.

    Args:
        config: Application configuration dict.
    """
    global _config
    _config = config


@router.get("/status")
def agent_status() -> dict:
    """Get agent status: last run time, status, and indicator color.

    Returns:
        Dict with status, last_run, indicator, tasks_created_today.
    """
    db = get_db()
    try:
        last_run = db.query(AgentRun).order_by(AgentRun.ran_at.desc()).first()

        if not last_run:
            return {
                "status": "no_runs",
                "last_run": None,
                "indicator": "amber",
                "tasks_created_today": 0,
            }

        now = datetime.utcnow()
        age = now - last_run.ran_at

        if last_run.status == "error":
            indicator = "red"
        elif age > timedelta(minutes=30):
            indicator = "amber"
        else:
            indicator = "green"

        # Count tasks created today
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_runs = (
            db.query(AgentRun)
            .filter(AgentRun.ran_at >= today_start)
            .all()
        )
        tasks_today = sum(r.tasks_created for r in today_runs)

        return {
            "status": last_run.status,
            "last_run": last_run.ran_at.isoformat(),
            "indicator": indicator,
            "tasks_created_today": tasks_today,
            "last_job": last_run.job_name,
            "last_error": last_run.error_message,
        }
    finally:
        db.close()


@router.post("/run")
def trigger_run() -> dict:
    """Trigger an immediate agent run.

    Returns:
        Dict with job_id for polling.
    """
    if not _config:
        raise HTTPException(status_code=500, detail="Config not loaded")
    job_id = start_manual_run(_config)
    return {"job_id": job_id}


@router.get("/run/{job_id}")
def poll_run(job_id: str) -> dict:
    """Poll the status of a manual agent run.

    Args:
        job_id: Job ID from trigger_run response.

    Returns:
        Dict with status (pending | running | done | error).
    """
    status = get_manual_run_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Run not found")
    return status
