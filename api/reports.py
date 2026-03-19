"""Daily report REST API endpoint."""

import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query

from database import get_db
from models import AgentRun, Goal, Task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _monday_of_week(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _fmt_date_long(d: date) -> str:
    """Format date like 'Thursday, Mar 19'."""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{days[d.weekday()]}, {months[d.month - 1]} {d.day}"


def _fmt_date_short(d: date) -> str:
    """Format date like 'Mar 16'."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[d.month - 1]} {d.day}"


@router.get("/daily")
def daily_report(date_str: str | None = Query(None, alias="date")) -> dict:
    """Generate a daily report with goals, tasks, and agent stats.

    Args:
        date_str: Date as YYYY-MM-DD query param (?date=...). Defaults to today.

    Returns:
        Dict with 'json' (structured data) and 'slack_text' (formatted string).
    """
    db = get_db()
    try:
        if date_str:
            report_date = date.fromisoformat(date_str)
        else:
            report_date = date.today()

        monday = _monday_of_week(report_date)

        # ---- Goals ----
        goals = (
            db.query(Goal)
            .filter(
                (Goal.week_start == monday)
                | ((Goal.status == "active") & (Goal.week_start < monday))
            )
            .filter(Goal.status != "archived")
            .order_by(Goal.sort_order, Goal.created_at)
            .all()
        )

        # ---- Tasks completed today ----
        day_start = datetime(report_date.year, report_date.month, report_date.day)
        day_end = day_start + timedelta(days=1)

        completed_today = (
            db.query(Task)
            .filter(Task.done == True, Task.updated_at >= day_start, Task.updated_at < day_end)
            .order_by(Task.priority, Task.updated_at)
            .all()
        )

        # ---- Open P1s ----
        open_p1s = (
            db.query(Task)
            .filter(Task.done == False, Task.priority == "p1")
            .order_by(Task.created_at)
            .all()
        )

        # ---- Gaps detected today ----
        gaps_today = (
            db.query(Task)
            .filter(
                Task.source == "jira_gap",
                Task.done == False,
                Task.created_at >= day_start,
                Task.created_at < day_end,
            )
            .order_by(Task.priority, Task.created_at)
            .all()
        )

        # ---- Stale/blocked (all open auto tasks with stale or blocked in title) ----
        stale_blocked = (
            db.query(Task)
            .filter(
                Task.done == False,
                Task.auto == True,
                Task.title.ilike("%stale%") | Task.title.ilike("%blocked%"),
            )
            .order_by(Task.priority, Task.created_at)
            .all()
        )

        # ---- Agent stats ----
        today_start = datetime(report_date.year, report_date.month, report_date.day)
        today_runs = (
            db.query(AgentRun)
            .filter(AgentRun.ran_at >= today_start, AgentRun.ran_at < day_end)
            .order_by(AgentRun.ran_at.desc())
            .all()
        )
        total_runs = len(today_runs)
        total_created = sum(r.tasks_created for r in today_runs)
        total_updated = sum(r.tasks_updated for r in today_runs)
        last_run = today_runs[0] if today_runs else None
        last_run_time = last_run.ran_at.strftime("%H:%M") if last_run else "—"
        last_run_ok = last_run.status == "success" if last_run else False

        # ---- Open task count ----
        open_count = db.query(Task).filter(Task.done == False).count()

        # ---- Build JSON ----
        report_json = {
            "date": report_date.isoformat(),
            "goals": [g.to_dict() for g in goals],
            "completed_today": [t.to_dict() for t in completed_today],
            "open_p1s": [t.to_dict() for t in open_p1s],
            "gaps_today": [t.to_dict() for t in gaps_today],
            "stale_blocked": [t.to_dict() for t in stale_blocked],
            "agent_stats": {
                "runs": total_runs,
                "tasks_created": total_created,
                "tasks_updated": total_updated,
                "last_run_time": last_run_time,
                "last_run_ok": last_run_ok,
            },
            "summary": {
                "open": open_count,
                "done_today": len(completed_today),
            },
        }

        # ---- Build Slack text ----
        lines = []
        lines.append(f"*Daily Report — {_fmt_date_long(report_date)}*")
        lines.append("")

        # Goals
        if goals:
            lines.append(f"*CTO Goals (Week of {_fmt_date_short(monday)})*")
            for g in goals:
                if g.status == "completed":
                    icon = "\u2705"
                else:
                    icon = "\U0001f535"
                notes = f" — {g.progress_notes}" if g.progress_notes else ""
                lines.append(f"  {icon} {g.title}{notes}")
            lines.append("")

        # Completed today
        lines.append(f"*Completed Today ({len(completed_today)})*")
        if completed_today:
            for t in completed_today:
                prefix = f"[{t.jira_key}] " if t.jira_key else ""
                lines.append(f"  - {prefix}{t.title}")
        else:
            lines.append("  - (none)")
        lines.append("")

        # Open P1s
        lines.append(f"*Open P1s ({len(open_p1s)})*")
        if open_p1s:
            for t in open_p1s:
                prefix = f"[{t.jira_key}] " if t.jira_key else ""
                lines.append(f"  - \U0001f534 {prefix}{t.title}")
        else:
            lines.append("  - (none)")
        lines.append("")

        # Gaps detected today
        if gaps_today:
            lines.append(f"*Gaps Detected Today ({len(gaps_today)})*")
            for t in gaps_today:
                prefix = f"[{t.jira_key}] " if t.jira_key else ""
                lines.append(f"  - {prefix}{t.title}")
            lines.append("")

        # Stale/blocked
        if stale_blocked:
            lines.append(f"*Stale / Blocked ({len(stale_blocked)})*")
            for t in stale_blocked:
                prefix = f"[{t.jira_key}] " if t.jira_key else ""
                lines.append(f"  - {prefix}{t.title}")
            lines.append("")

        # Agent stats
        check = "\u2713" if last_run_ok else "\u2717"
        lines.append("*Agent Stats*")
        lines.append(f"  Runs: {total_runs} | Created: {total_created} | Updated: {total_updated} | Last: {last_run_time} {check}")
        lines.append("")

        # Summary
        indicator = "\U0001f7e2" if last_run_ok else "\U0001f534"
        lines.append(f"*Summary:* {open_count} open | {len(completed_today)} done today | Agent: {indicator}")

        slack_text = "\n".join(lines)

        return {"json": report_json, "slack_text": slack_text}
    finally:
        db.close()
