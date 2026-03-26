"""Task action endpoints — AI Analysis, Ranking Rationale, and Comment Suggestion."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.claude_runner import run_claude_suggest, write_context_file
from agent.jira_client import JiraClient
from config import CONFIG
from database import get_db
from models import Task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["task-actions"])

PRIORITY_LABELS = {
    "p1": "P1 — CEO Escalation",
    "p2": "P2 — Company Priority",
    "p3": "P3 — This Week",
    "p4": "P4 — Backlog",
}


def _get_jira_client() -> JiraClient:
    return JiraClient(
        base_url=CONFIG["JIRA_BASE_URL"],
        email=CONFIG["JIRA_EMAIL"],
        api_token=CONFIG["JIRA_API_TOKEN"],
    )


def _days_ago(iso_str: str | None) -> int | None:
    """Return number of days between now and an ISO datetime string."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).days
    except Exception:
        return None


def _build_suggested_actions(detail: dict) -> list[str]:
    """Generate rule-based suggested next actions from Jira issue data."""
    actions = []

    if not detail.get("assignee"):
        actions.append("Assign someone — ticket is unassigned")

    if detail.get("story_points") is None:
        actions.append("Add story points — helps sprint planning")

    stale_days = _days_ago(detail.get("updated"))
    if stale_days is not None and stale_days >= 3:
        actions.append(f"Follow up — stale for {stale_days} days")

    if detail.get("blockers"):
        open_blockers = [b for b in detail["blockers"] if b.get("status", "").lower() != "done"]
        if open_blockers:
            keys = ", ".join(b["key"] for b in open_blockers)
            actions.append(f"Unblock — blocked by {keys}")

    status = (detail.get("status") or "").lower()
    if "review" in status or "pr" in status:
        actions.append("Review PR — ticket is in review status")

    if not detail.get("due_date") and detail.get("priority") in ("Highest", "High"):
        actions.append("Set a due date or fixVersion — high-priority ticket without deadline")

    if detail.get("comments_count", 0) == 0:
        actions.append("Add context — no comments on this ticket yet")

    if not actions:
        actions.append("Looks good — no obvious gaps detected")

    return actions


@router.get("/{task_id}/analysis")
def get_task_analysis(task_id: str) -> dict:
    """Fetch live Jira data for a task and return AI analysis with suggested actions."""
    db = get_db()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not task.jira_key:
            raise HTTPException(status_code=400, detail="Task has no linked Jira ticket")

        client = _get_jira_client()
        detail = client.get_issue_detail(task.jira_key)

        age_days = _days_ago(detail.get("created"))
        stale_days = _days_ago(detail.get("updated"))

        summary_parts = [f"[{detail['key']}] {detail['summary']}"]
        summary_parts.append(f"Status: {detail['status']} | Priority: {detail['priority']}")
        if detail.get("assignee"):
            summary_parts.append(f"Assignee: {detail['assignee']}")
        else:
            summary_parts.append("Assignee: Unassigned")
        if age_days is not None:
            summary_parts.append(f"Age: {age_days}d | Last updated: {stale_days}d ago")

        actions = _build_suggested_actions(detail)

        return {
            "summary": " · ".join(summary_parts),
            "fields": {
                "key": detail["key"],
                "title": detail["summary"],
                "status": detail["status"],
                "assignee": detail.get("assignee"),
                "priority": detail["priority"],
                "story_points": detail.get("story_points"),
                "description_snippet": detail.get("description_snippet"),
                "due_date": detail.get("due_date"),
                "due_date_source": detail.get("due_date_source"),
                "fix_versions": detail.get("fix_versions", []),
                "age_days": age_days,
                "stale_days": stale_days,
                "comments_count": detail.get("comments_count", 0),
                "blockers": detail.get("blockers", []),
            },
            "actions": actions,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to analyze task {task_id}")
        raise HTTPException(status_code=502, detail=f"Jira API error: {e}")
    finally:
        db.close()


@router.get("/{task_id}/ranking")
def get_task_ranking(task_id: str) -> dict:
    """Return ranking factors and explanation for a task's position."""
    db = get_db()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Determine position among all open tasks (same sort as list endpoint)
        all_tasks = db.query(Task).order_by(Task.done, Task.priority, Task.created_at).all()
        position = None
        open_count = 0
        for i, t in enumerate(all_tasks):
            if t.done:
                continue
            open_count += 1
            if t.id == task_id:
                position = open_count

        # Parse gap type from title prefix patterns
        gap_type = None
        title = task.title or ""
        gap_prefixes = {
            "[UNASSIGNED]": "unassigned",
            "[NO POINTS]": "no_points",
            "[STALE]": "stale",
            "[BLOCKED]": "blocked",
            "[NO PR]": "no_pr",
        }
        for prefix, gtype in gap_prefixes.items():
            if title.upper().startswith(prefix):
                gap_type = gtype
                break

        age_days = None
        if task.created_at:
            age_days = (datetime.utcnow() - task.created_at).days

        factors = {
            "priority": task.priority,
            "priority_label": PRIORITY_LABELS.get(task.priority, task.priority),
            "source": task.source,
            "auto": task.auto,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "age_days": age_days,
            "category": task.category,
            "has_jira_link": bool(task.jira_key),
            "jira_key": task.jira_key,
            "gap_type": gap_type,
            "done": task.done,
        }

        # Build human-readable explanation
        parts = []
        parts.append(f"{PRIORITY_LABELS.get(task.priority, task.priority)}")
        if task.auto:
            parts.append(f"auto-generated ({task.source})")
        else:
            parts.append("user-created")
        if gap_type:
            parts.append(f"gap type: {gap_type}")
        if task.jira_key:
            parts.append(f"linked to {task.jira_key}")
        if age_days is not None:
            if age_days == 0:
                parts.append("created today")
            else:
                parts.append(f"created {age_days}d ago")

        explanation = " · ".join(parts)

        sort_explanation = "Tasks sorted by: done → priority → created_at."
        if position and not task.done:
            sort_explanation += f" This task is position {position} of {open_count} open tasks"
            sort_explanation += f" because it's {task.priority.upper()}"
            if age_days is not None and age_days > 0:
                sort_explanation += f" and was created {age_days}d ago"
            sort_explanation += "."

        return {
            "factors": factors,
            "explanation": explanation,
            "sort_explanation": sort_explanation,
            "position": position,
            "open_count": open_count,
        }
    finally:
        db.close()


@router.post("/{task_id}/suggest-comment")
async def suggest_comment(task_id: str) -> dict:
    """Fetch Jira ticket + comments, run Claude to suggest a comment."""
    db = get_db()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not task.jira_key:
            raise HTTPException(status_code=400, detail="Task has no linked Jira ticket")

        client = _get_jira_client()
        detail = client.get_issue_detail(task.jira_key)
        comments = client.get_issue_comments(task.jira_key)

        write_context_file(task.jira_key, detail, comments)

        result = await run_claude_suggest(task.jira_key)

        return {
            "jira_key": task.jira_key,
            "summary": result.get("summary", ""),
            "suggested_comment": result.get("suggested_comment", ""),
            "comments_count": len(comments),
            "error": result.get("error"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to suggest comment for task {task_id}")
        raise HTTPException(status_code=502, detail=f"Error: {e}")
    finally:
        db.close()


class PostCommentRequest(BaseModel):
    comment: str


@router.post("/{task_id}/post-comment")
def post_comment(task_id: str, body: PostCommentRequest) -> dict:
    """Post a comment to the linked Jira ticket."""
    db = get_db()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not task.jira_key:
            raise HTTPException(status_code=400, detail="Task has no linked Jira ticket")

        client = _get_jira_client()
        result = client.add_comment(task.jira_key, body.comment)

        return {
            "success": True,
            "jira_key": task.jira_key,
            "comment_id": result.get("id"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to post comment for task {task_id}")
        raise HTTPException(status_code=502, detail=f"Jira API error: {e}")
    finally:
        db.close()
