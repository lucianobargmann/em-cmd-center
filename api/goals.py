"""Goals CRUD REST API endpoints with Jira sync."""

import logging
from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.jira_client import JiraClient
from database import get_db
from models import Goal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/goals", tags=["goals"])

_config: dict = {}


def set_goals_config(config: dict) -> None:
    """Store config reference for Jira sync.

    Args:
        config: Application configuration dict.
    """
    global _config
    _config = config


def _get_jira_client() -> JiraClient | None:
    """Create a JiraClient from config, or None if not configured."""
    if not _config.get("JIRA_BASE_URL"):
        return None
    return JiraClient(
        base_url=_config["JIRA_BASE_URL"],
        email=_config["JIRA_EMAIL"],
        api_token=_config["JIRA_API_TOKEN"],
    )


def _monday_of_week(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - __import__("datetime").timedelta(days=d.weekday())


# ---- Pydantic models ----

class GoalCreate(BaseModel):
    """Request body for creating a goal."""
    title: str
    description: str | None = None
    week_start: str | None = None  # YYYY-MM-DD, defaults to current week Monday
    progress_notes: str | None = None


class GoalUpdate(BaseModel):
    """Request body for updating a goal (all fields optional)."""
    title: str | None = None
    description: str | None = None
    status: str | None = None  # active, completed, archived
    progress_notes: str | None = None
    sort_order: int | None = None


# ---- Endpoints ----

@router.get("")
def list_goals(week_start: str | None = None) -> list[dict]:
    """List goals for a given week, plus carry-forward active goals.

    Args:
        week_start: Monday date as YYYY-MM-DD. Defaults to current week.

    Returns:
        List of goal dicts.
    """
    db = get_db()
    try:
        if week_start:
            ws = date.fromisoformat(week_start)
        else:
            ws = _monday_of_week(date.today())

        # Goals for this specific week OR active goals from earlier weeks
        goals = (
            db.query(Goal)
            .filter(
                (Goal.week_start == ws)
                | ((Goal.status == "active") & (Goal.week_start < ws))
            )
            .order_by(Goal.sort_order, Goal.created_at)
            .all()
        )
        return [g.to_dict() for g in goals]
    finally:
        db.close()


@router.post("")
def create_goal(body: GoalCreate) -> dict:
    """Create a new goal and sync to Jira.

    Args:
        body: Goal creation data.

    Returns:
        Created goal dict.
    """
    db = get_db()
    try:
        if body.week_start:
            ws = date.fromisoformat(body.week_start)
        else:
            ws = _monday_of_week(date.today())

        goal = Goal(
            title=body.title,
            description=body.description,
            week_start=ws,
            progress_notes=body.progress_notes,
        )

        # Sync to Jira
        jira = _get_jira_client()
        em_project = _config.get("JIRA_EM_PROJECT", "EM-TASKS")
        if jira and em_project:
            try:
                result = jira.create_issue(
                    project_key=em_project,
                    summary=f"[Goal] {body.title}",
                    description=body.description or "",
                )
                goal.jira_key = result["key"]
                goal.jira_url = result["url"]
            except Exception:
                logger.exception("Failed to create Jira issue for goal")

        db.add(goal)
        db.commit()
        db.refresh(goal)
        return goal.to_dict()
    finally:
        db.close()


@router.patch("/{goal_id}")
def update_goal(goal_id: str, body: GoalUpdate) -> dict:
    """Update a goal and sync changes to Jira.

    Args:
        goal_id: UUID of the goal.
        body: Fields to update.

    Returns:
        Updated goal dict.
    """
    db = get_db()
    try:
        goal = db.query(Goal).filter(Goal.id == goal_id).first()
        if not goal:
            raise HTTPException(status_code=404, detail="Goal not found")

        update_data = body.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(goal, key, value)
        goal.updated_at = datetime.utcnow()

        # Sync to Jira
        if goal.jira_key:
            jira = _get_jira_client()
            if jira:
                try:
                    jira_fields = {}
                    if "title" in update_data:
                        jira_fields["summary"] = f"[Goal] {update_data['title']}"
                    if "description" in update_data:
                        jira_fields["description"] = update_data["description"] or ""

                    transition = None
                    if "status" in update_data:
                        if update_data["status"] == "completed":
                            transition = "Done"
                        elif update_data["status"] == "active":
                            transition = "To Do"

                    if jira_fields or transition:
                        jira.update_issue(
                            issue_key=goal.jira_key,
                            fields=jira_fields or None,
                            transition_name=transition,
                        )
                except Exception:
                    logger.exception("Failed to sync goal update to Jira")

        db.commit()
        db.refresh(goal)
        return goal.to_dict()
    finally:
        db.close()


@router.delete("/{goal_id}")
def archive_goal(goal_id: str) -> dict:
    """Archive a goal (soft delete). Jira issue is kept.

    Args:
        goal_id: UUID of the goal.

    Returns:
        Confirmation dict.
    """
    db = get_db()
    try:
        goal = db.query(Goal).filter(Goal.id == goal_id).first()
        if not goal:
            raise HTTPException(status_code=404, detail="Goal not found")
        goal.status = "archived"
        goal.updated_at = datetime.utcnow()
        db.commit()
        return {"ok": True}
    finally:
        db.close()
