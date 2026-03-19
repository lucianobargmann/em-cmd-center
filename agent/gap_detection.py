"""Jira gap detection — scans active sprints for common issues."""

import logging
from datetime import date, datetime, timedelta

from database import get_db
from models import AgentRun, Task

logger = logging.getLogger(__name__)

# Gap type prefixes used for deduplication
GAP_MISSING_SP = "Missing story points"
GAP_MISSING_AC = "Missing AC"
GAP_NO_PR = "No PR found for in-progress ticket"
GAP_UNASSIGNED = "Unassigned ticket in sprint"
GAP_STALE = "Stale ticket"
GAP_BLOCKED = "Blocked with no blocker defined"
GAP_OVERDUE = "Overdue"
GAP_MID_SPRINT = "Mid-sprint addition"
GAP_AGING = "Aging backlog ticket"


def _gap_task_exists(db, jira_key: str, gap_prefix: str) -> bool:
    """Check if an open gap task already exists for this key and gap type."""
    existing = (
        db.query(Task)
        .filter(
            Task.jira_key == jira_key,
            Task.title.contains(gap_prefix),
            Task.done == False,
        )
        .first()
    )
    return existing is not None


def _create_gap_task(
    db, jira_key: str, title: str, priority: str, base_url: str, source: str = "jira_gap"
) -> Task:
    """Create a gap detection task in the database."""
    task = Task(
        title=title,
        priority=priority,
        category="delivery",
        done=False,
        auto=True,
        jira_key=jira_key,
        jira_url=f"{base_url}/browse/{jira_key}",
        source=source,
    )
    db.add(task)
    return task


def _resolve_fixed_gaps(db, jira_key: str, gap_prefix: str) -> int:
    """Mark gap tasks as done if the underlying issue is resolved."""
    tasks = (
        db.query(Task)
        .filter(
            Task.jira_key == jira_key,
            Task.title.contains(gap_prefix),
            Task.done == False,
        )
        .all()
    )
    count = 0
    for t in tasks:
        t.done = True
        t.updated_at = datetime.utcnow()
        count += 1
    return count


def run_gap_detection(jira_client, team_projects: list[str], base_url: str) -> dict:
    """Run gap detection across all team projects.

    Args:
        jira_client: Initialized JiraClient.
        team_projects: List of Jira project keys.
        base_url: Jira base URL for building links.

    Returns:
        Dict with tasks_created and tasks_updated counts.
    """
    db = get_db()
    created = 0
    updated = 0

    try:
        for project in team_projects:
            try:
                issues = jira_client.get_active_sprint_issues(project)
            except Exception as e:
                logger.error(f"Failed to fetch issues for {project}: {e}")
                continue

            for issue in issues:
                key = issue["key"]
                fields = issue.get("fields", {})
                summary = fields.get("summary", "")
                status_name = fields.get("status", {}).get("name", "")
                assignee = fields.get("assignee")
                story_points = fields.get("customfield_10016")
                description = fields.get("description")
                due_date_str = fields.get("duedate")
                issue_links = fields.get("issuelinks", [])

                # 1. Missing story points
                if story_points is None:
                    if not _gap_task_exists(db, key, GAP_MISSING_SP):
                        _create_gap_task(
                            db, key,
                            f"[{key}] Missing story points — {summary}",
                            "p2", base_url,
                        )
                        created += 1
                else:
                    updated += _resolve_fixed_gaps(db, key, GAP_MISSING_SP)

                # 2. Missing acceptance criteria
                has_ac = False
                if description:
                    if isinstance(description, dict):
                        desc_text = str(description)
                    else:
                        desc_text = str(description)
                    has_ac = any(
                        term in desc_text.lower()
                        for term in ["acceptance criteria", "given", "when", "then", "ac:"]
                    )
                if not has_ac:
                    if not _gap_task_exists(db, key, GAP_MISSING_AC):
                        _create_gap_task(
                            db, key,
                            f"[{key}] Missing AC — {summary}",
                            "p2", base_url,
                        )
                        created += 1
                else:
                    updated += _resolve_fixed_gaps(db, key, GAP_MISSING_AC)

                # 3. In Progress, no PR linked
                in_progress_states = ["In Progress", "Dev In Progress", "In Review"]
                if status_name in in_progress_states:
                    has_pr = jira_client.get_issue_has_pr(key)
                    if not has_pr:
                        if not _gap_task_exists(db, key, GAP_NO_PR):
                            _create_gap_task(
                                db, key,
                                f"[{key}] No PR found for in-progress ticket — {summary}",
                                "p2", base_url,
                            )
                            created += 1
                    else:
                        updated += _resolve_fixed_gaps(db, key, GAP_NO_PR)

                # 4. Unassigned in active sprint
                if assignee is None:
                    if not _gap_task_exists(db, key, GAP_UNASSIGNED):
                        _create_gap_task(
                            db, key,
                            f"[{key}] Unassigned ticket in sprint — {summary}",
                            "p1", base_url,
                        )
                        created += 1
                else:
                    updated += _resolve_fixed_gaps(db, key, GAP_UNASSIGNED)

                # 5. Stale (In Progress, no update > 3 days)
                if status_name in in_progress_states:
                    is_stale, n_days = jira_client.is_stale(issue, days=3)
                    if is_stale:
                        if not _gap_task_exists(db, key, GAP_STALE):
                            _create_gap_task(
                                db, key,
                                f"[{key}] Stale ticket — no update in {n_days} days — {summary}",
                                "p1", base_url,
                            )
                            created += 1
                    else:
                        updated += _resolve_fixed_gaps(db, key, GAP_STALE)

                # 6. Blocked, no blocker linked
                if status_name.lower() == "blocked":
                    has_blocker = any(
                        link.get("type", {}).get("name", "").lower() in ["blocks", "is blocked by"]
                        for link in issue_links
                    )
                    if not has_blocker:
                        if not _gap_task_exists(db, key, GAP_BLOCKED):
                            _create_gap_task(
                                db, key,
                                f"[{key}] Blocked with no blocker defined — {summary}",
                                "p1", base_url,
                            )
                            created += 1
                    else:
                        updated += _resolve_fixed_gaps(db, key, GAP_BLOCKED)

                # 7. Overdue
                if due_date_str:
                    due = date.fromisoformat(due_date_str)
                    if due < date.today() and status_name.lower() != "done":
                        if not _gap_task_exists(db, key, GAP_OVERDUE):
                            _create_gap_task(
                                db, key,
                                f"[{key}] Overdue — {summary}",
                                "p1", base_url,
                            )
                            created += 1
                    else:
                        updated += _resolve_fixed_gaps(db, key, GAP_OVERDUE)

                # 8. Mid-sprint addition (created in last 7 days = likely mid-sprint)
                created_str = fields.get("created", "")
                if created_str:
                    created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    now = datetime.now(created_dt.tzinfo)
                    if (now - created_dt) < timedelta(days=7):
                        if not _gap_task_exists(db, key, GAP_MID_SPRINT):
                            _create_gap_task(
                                db, key,
                                f"[{key}] Mid-sprint addition — {summary}",
                                "p3", base_url,
                            )
                            created += 1

                # 9. Backlog aging (no activity > 30 days)
                updated_str = fields.get("updated", "")
                if updated_str and status_name.lower() in ["to do", "backlog", "open"]:
                    updated_dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                    now = datetime.now(updated_dt.tzinfo)
                    if (now - updated_dt) > timedelta(days=30):
                        if not _gap_task_exists(db, key, GAP_AGING):
                            _create_gap_task(
                                db, key,
                                f"[{key}] Aging backlog ticket — {summary}",
                                "p4", base_url,
                            )
                            created += 1

        db.commit()

        # Log the run
        run = AgentRun(
            job_name="gap_detection",
            status="success",
            tasks_created=created,
            tasks_updated=updated,
        )
        db.add(run)
        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Gap detection failed: {e}")
        run = AgentRun(
            job_name="gap_detection",
            status="error",
            tasks_created=created,
            tasks_updated=updated,
            error_message=str(e),
        )
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()

    return {"tasks_created": created, "tasks_updated": updated}
