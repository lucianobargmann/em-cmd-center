"""Recurring daily task creation based on day of week."""

import logging
from datetime import date, datetime

from database import get_db
from models import AgentRun, Task

logger = logging.getLogger(__name__)

DAILY_TASKS = [
    {"title": "Email triage — process Outlook inbox", "pri": "p3", "cat": "email"},
    {"title": "Standup review — check async updates, identify blockers", "pri": "p3", "cat": "people"},
    {"title": "Ticket hygiene — check for new gaps since yesterday", "pri": "p3", "cat": "delivery"},
    {"title": "End-of-day note — what moved, what is at risk", "pri": "p4", "cat": "other"},
]

MONDAY_EXTRAS = [
    {"title": "Sprint kickoff review — all tickets need assignee, SP, AC", "pri": "p3", "cat": "delivery"},
    {"title": "Automation brief review — read Jira gap detection report", "pri": "p3", "cat": "delivery"},
]

WEDNESDAY_EXTRAS = [
    {"title": "Mid-sprint health check — flag at-risk tickets now", "pri": "p3", "cat": "delivery"},
    {"title": "Hiring pipeline review — CV shortlist and interview schedule", "pri": "p3", "cat": "hiring"},
]

FRIDAY_EXTRAS = [
    {"title": "Stack rank review — check trends, note 1:1 talking points", "pri": "p3", "cat": "reports"},
    {"title": "Leadership report — finalize and send by 15:00", "pri": "p3", "cat": "reports"},
    {"title": "Infra cost check — approve optimization recommendations", "pri": "p3", "cat": "infra"},
    {"title": "Sprint retro note — one improvement item in Jira", "pri": "p3", "cat": "delivery"},
]


def _task_exists_today(db, title: str) -> bool:
    """Check if a task with this title was already created today."""
    today = date.today()
    existing = (
        db.query(Task)
        .filter(Task.title == title)
        .all()
    )
    for t in existing:
        if t.created_at and t.created_at.date() == today:
            return True
    return False


def create_daily_tasks() -> dict:
    """Create daily recurring tasks based on the current day of week.

    Returns:
        Dict with tasks_created count.
    """
    db = get_db()
    created = 0
    today = date.today()
    weekday = today.weekday()  # 0=Monday, 4=Friday

    try:
        tasks_to_create = list(DAILY_TASKS)

        if weekday == 0:  # Monday
            tasks_to_create.extend(MONDAY_EXTRAS)
        elif weekday == 2:  # Wednesday
            tasks_to_create.extend(WEDNESDAY_EXTRAS)
        elif weekday == 4:  # Friday
            tasks_to_create.extend(FRIDAY_EXTRAS)

        for task_def in tasks_to_create:
            if not _task_exists_today(db, task_def["title"]):
                task = Task(
                    title=task_def["title"],
                    priority=task_def["pri"],
                    category=task_def["cat"],
                    done=False,
                    auto=True,
                    source="jira_recurring",
                )
                db.add(task)
                created += 1

        db.commit()

        run = AgentRun(
            job_name="daily_tasks",
            status="success",
            tasks_created=created,
            tasks_updated=0,
        )
        db.add(run)
        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Daily task creation failed: {e}")
        run = AgentRun(
            job_name="daily_tasks",
            status="error",
            error_message=str(e),
        )
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()

    return {"tasks_created": created}
