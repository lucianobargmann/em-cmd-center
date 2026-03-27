"""APScheduler setup and job registration."""

import logging
import threading
import uuid
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent.daily_tasks import create_daily_tasks
from agent.gap_detection import run_gap_detection, sync_priority_labels
from agent.git_stats import fetch_all_remotes
from agent.infra_costs import run_infra_cost_check
from agent.jira_client import JiraClient
from agent.metrics_collector import collect_weekly_metrics
from agent.stack_rank import run_stack_rank
from database import get_db
from models import AgentRun

logger = logging.getLogger(__name__)

# Track manual run status
_manual_runs: dict[str, dict] = {}
_manual_runs_lock = threading.Lock()


def _parse_cron(cron_str: str) -> dict:
    """Parse a cron string into APScheduler CronTrigger kwargs.

    Args:
        cron_str: Standard cron format 'minute hour day month day_of_week'.

    Returns:
        Dict suitable for CronTrigger.
    """
    parts = cron_str.strip().split()
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def _run_full_agent(config: dict) -> None:
    """Run the complete agent cycle: gap detection + daily tasks.

    Args:
        config: Application configuration dict.
    """
    try:
        client = JiraClient(
            config["JIRA_BASE_URL"],
            config["JIRA_EMAIL"],
            config["JIRA_API_TOKEN"],
        )
        run_gap_detection(client, config["JIRA_TEAM_PROJECTS"], config["JIRA_BASE_URL"])
    except Exception as e:
        logger.error(f"Gap detection job failed: {e}")

    try:
        client = JiraClient(
            config["JIRA_BASE_URL"],
            config["JIRA_EMAIL"],
            config["JIRA_API_TOKEN"],
        )
        sync_priority_labels(client, config["JIRA_TEAM_PROJECTS"], config["JIRA_BASE_URL"])
    except Exception as e:
        logger.error(f"Priority label sync failed: {e}")

    try:
        create_daily_tasks()
    except Exception as e:
        logger.error(f"Daily tasks job failed: {e}")


def _run_velocity_summary(config: dict) -> None:
    """Run the weekly velocity + sprint summary job."""
    db = get_db()
    try:
        client = JiraClient(
            config["JIRA_BASE_URL"],
            config["JIRA_EMAIL"],
            config["JIRA_API_TOKEN"],
        )

        summaries = []
        for project in config["JIRA_TEAM_PROJECTS"]:
            try:
                issues = client.get_completed_sprint_data(project)
                total_sp = sum(
                    (i.get("fields", {}).get("customfield_10016") or 0) for i in issues
                )
                done_count = len(issues)
                summaries.append(f"{project}: {done_count} tickets, {total_sp} SP delivered")
            except Exception as e:
                summaries.append(f"{project}: failed to fetch — {e}")

        notes = "Weekly Sprint Summary\n" + "\n".join(summaries)

        from models import Task
        task = Task(
            title="Delivery summary ready — review before sending report",
            priority="p3",
            category="reports",
            auto=True,
            source="jira_recurring",
            notes=notes,
        )
        db.add(task)

        run = AgentRun(
            job_name="velocity_summary",
            status="success",
            tasks_created=1,
            tasks_updated=0,
        )
        db.add(run)
        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Velocity summary failed: {e}")
        run = AgentRun(job_name="velocity_summary", status="error", error_message=str(e))
        db.add(run)
        db.commit()
    finally:
        db.close()


def _run_stack_rank(config: dict) -> None:
    """Run the stack rank job."""
    try:
        client = JiraClient(
            config["JIRA_BASE_URL"],
            config["JIRA_EMAIL"],
            config["JIRA_API_TOKEN"],
        )
        run_stack_rank(client, config["JIRA_TEAM_PROJECTS"], config.get("STACK_RANK_SCRIPT", ""))
    except Exception as e:
        logger.error(f"Stack rank job failed: {e}")


def _run_infra_costs(config: dict) -> None:
    """Run the infra cost check job."""
    try:
        run_infra_cost_check(config.get("CLOUD_PROVIDER", ""))
    except Exception as e:
        logger.error(f"Infra cost job failed: {e}")


def _run_weekly_metrics(config: dict) -> None:
    """Run the weekly developer metrics collection job."""
    try:
        collect_weekly_metrics(config)
    except Exception as e:
        logger.error(f"Weekly metrics collection failed: {e}")


def _run_git_fetch_and_metrics(config: dict) -> None:
    """Fetch all git remotes then re-collect weekly metrics."""
    repos_dir = config.get("GIT_REPOS_DIR", "")
    if not repos_dir:
        return
    try:
        fetch_all_remotes(repos_dir)
    except Exception as e:
        logger.error(f"Git fetch failed: {e}")
    try:
        collect_weekly_metrics(config)
    except Exception as e:
        logger.error(f"Metrics collection (git fetch job) failed: {e}")


def _run_slack_sp_reminders(config: dict) -> None:
    """Run the Slack story point reminder job."""
    try:
        from agent.slack_reminders import send_sp_reminders
        send_sp_reminders(config)
    except Exception as e:
        logger.error(f"Slack SP reminder job failed: {e}")


def start_manual_run(config: dict) -> str:
    """Trigger an immediate agent run in a background thread.

    Args:
        config: Application configuration dict.

    Returns:
        Job ID string for status polling.
    """
    job_id = str(uuid.uuid4())
    with _manual_runs_lock:
        _manual_runs[job_id] = {"status": "pending", "started_at": datetime.utcnow().isoformat()}

    def _run():
        with _manual_runs_lock:
            _manual_runs[job_id]["status"] = "running"
        try:
            _run_full_agent(config)
            with _manual_runs_lock:
                _manual_runs[job_id]["status"] = "done"
        except Exception as e:
            with _manual_runs_lock:
                _manual_runs[job_id]["status"] = "error"
                _manual_runs[job_id]["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return job_id


def get_manual_run_status(job_id: str) -> dict | None:
    """Get the status of a manual agent run.

    Args:
        job_id: Job ID returned by start_manual_run.

    Returns:
        Status dict or None if not found.
    """
    with _manual_runs_lock:
        return _manual_runs.get(job_id)


def setup_scheduler(config: dict) -> BackgroundScheduler:
    """Create and start the APScheduler with all configured jobs.

    Args:
        config: Application configuration dict.

    Returns:
        Running BackgroundScheduler instance.
    """
    scheduler = BackgroundScheduler()

    # Periodic Jira poll + daily tasks
    scheduler.add_job(
        _run_full_agent,
        IntervalTrigger(minutes=config["POLL_INTERVAL_MINUTES"]),
        args=[config],
        id="jira_poll",
        name="Jira poll + daily tasks",
    )

    # Gap detection (Monday 06:00)
    gap_cron = _parse_cron(config["GAP_DETECTION_CRON"])
    scheduler.add_job(
        _run_full_agent,
        CronTrigger(**gap_cron),
        args=[config],
        id="gap_detection",
        name="Gap detection (Monday)",
    )

    # Daily tasks (weekdays at 08:00)
    scheduler.add_job(
        create_daily_tasks,
        CronTrigger(hour=8, minute=0, day_of_week="mon-fri"),
        id="daily_tasks",
        name="Daily recurring tasks",
    )

    # Velocity summary (Friday 07:00)
    scheduler.add_job(
        _run_velocity_summary,
        CronTrigger(hour=7, minute=0, day_of_week="fri"),
        args=[config],
        id="velocity_summary",
        name="Weekly velocity summary",
    )

    # Stack rank (from config cron)
    sr_cron = _parse_cron(config["STACK_RANK_CRON"])
    scheduler.add_job(
        _run_stack_rank,
        CronTrigger(**sr_cron),
        args=[config],
        id="stack_rank",
        name="Stack rank",
    )

    # Infra cost check (Friday 08:00)
    if config.get("CLOUD_PROVIDER"):
        scheduler.add_job(
            _run_infra_costs,
            CronTrigger(hour=8, minute=0, day_of_week="fri"),
            args=[config],
            id="infra_costs",
            name="Infra cost check",
        )

    # Weekly developer metrics (Monday 07:00 by default)
    metrics_cron = _parse_cron(config.get("METRICS_COLLECTION_CRON", "0 7 * * 1"))
    scheduler.add_job(
        _run_weekly_metrics,
        CronTrigger(**metrics_cron),
        args=[config],
        id="weekly_metrics",
        name="Weekly developer metrics",
    )

    # Git fetch + metrics refresh (4x/day weekdays, only if repos dir set)
    if config.get("GIT_REPOS_DIR"):
        git_cron = _parse_cron(config.get("GIT_FETCH_CRON", "0 6,10,14,18 * * 1-5"))
        scheduler.add_job(
            _run_git_fetch_and_metrics,
            CronTrigger(**git_cron),
            args=[config],
            id="git_fetch_metrics",
            name="Git fetch + metrics refresh",
        )

    # Slack SP reminders (weekdays 8am by default, only if token set)
    if config.get("SLACK_BOT_TOKEN"):
        slack_cron = _parse_cron(config.get("SLACK_REMINDER_CRON", "0 8 * * 1-5"))
        scheduler.add_job(
            _run_slack_sp_reminders,
            CronTrigger(**slack_cron),
            args=[config],
            id="slack_sp_reminder",
            name="Slack SP reminder DMs",
        )

    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))
    return scheduler
