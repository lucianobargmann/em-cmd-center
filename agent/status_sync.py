"""Sync Jira ticket statuses and changelog into the local cache for the Status Board."""

import logging
import time
import uuid
from datetime import datetime

from agent.jira_client import JiraClient
from database import get_db
from models import AgentRun, TicketStatusCache, TicketStatusHistory

logger = logging.getLogger(__name__)


def _parse_jira_datetime(dt_str: str) -> datetime:
    """Parse Jira datetime string to a naive UTC datetime."""
    if not dt_str:
        return datetime.utcnow()
    # Jira returns e.g. "2026-04-14T09:00:00.000+0000"
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(dt_str)
        # Convert to naive UTC
        if dt.tzinfo is not None:
            from datetime import timezone
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return datetime.utcnow()


def _extract_status_transitions(changelog: list[dict], issue_created_at: datetime) -> list[dict]:
    """Parse changelog entries into a list of status transitions.

    Returns list of dicts with keys: from_status, to_status, transitioned_at, time_in_from_seconds.
    """
    transitions = []
    for entry in changelog:
        entry_time = _parse_jira_datetime(entry.get("created", ""))
        for item in entry.get("items", []):
            if item.get("field") == "status":
                transitions.append({
                    "from_status": item.get("fromString"),
                    "to_status": item.get("toString"),
                    "transitioned_at": entry_time,
                    "time_in_from_seconds": None,
                })

    # Sort by transition time
    transitions.sort(key=lambda t: t["transitioned_at"])

    # Calculate time_in_from_seconds between consecutive transitions
    prev_time = issue_created_at
    for t in transitions:
        delta = (t["transitioned_at"] - prev_time).total_seconds()
        t["time_in_from_seconds"] = max(0, int(delta))
        prev_time = t["transitioned_at"]

    return transitions


def sync_ticket_statuses(config: dict) -> None:
    """Fetch all tracked Jira tickets and sync their status history.

    Args:
        config: Application configuration dict with Jira credentials.
    """
    client = JiraClient(
        config["JIRA_BASE_URL"],
        config["JIRA_EMAIL"],
        config["JIRA_API_TOKEN"],
    )
    projects = config["JIRA_TEAM_PROJECTS"]
    now = datetime.utcnow()
    db = get_db()

    tickets_synced = 0
    transitions_upserted = 0

    try:
        all_issues = []

        # Fetch open tickets per project
        for project in projects:
            jql = f"project = {project} AND resolution = Unresolved ORDER BY created ASC"
            fields = ["summary", "status", "assignee", "priority", "created", "updated"]
            try:
                issues = client.search_issues(jql, fields)
                for issue in issues:
                    issue["_resolved"] = False
                all_issues.extend(issues)
            except Exception as e:
                logger.error(f"Failed to fetch open tickets for {project}: {e}")

        # Fetch recently closed tickets (last 28 days)
        for project in projects:
            jql = f"project = {project} AND resolved >= -28d ORDER BY resolved DESC"
            fields = ["summary", "status", "assignee", "priority", "created", "updated", "resolutiondate"]
            try:
                issues = client.search_issues(jql, fields)
                for issue in issues:
                    issue["_resolved"] = True
                all_issues.extend(issues)
            except Exception as e:
                logger.error(f"Failed to fetch closed tickets for {project}: {e}")

        # Deduplicate by issue key (open takes precedence)
        seen_keys: dict[str, dict] = {}
        for issue in all_issues:
            key = issue.get("key", "")
            if key not in seen_keys:
                seen_keys[key] = issue
            elif not issue["_resolved"]:
                # Prefer the open version
                seen_keys[key] = issue

        logger.info(f"[StatusSync] Found {len(seen_keys)} unique tickets across {len(projects)} projects")

        # Process each ticket
        for issue_key, issue in seen_keys.items():
            try:
                fields = issue.get("fields", {})
                project_key = issue_key.split("-")[0] if "-" in issue_key else ""
                summary = fields.get("summary", "")[:500]
                priority_obj = fields.get("priority") or {}
                priority = priority_obj.get("name") if isinstance(priority_obj, dict) else None
                assignee_obj = fields.get("assignee") or {}
                assignee_account_id = assignee_obj.get("accountId") if isinstance(assignee_obj, dict) else None
                assignee_display_name = assignee_obj.get("displayName") if isinstance(assignee_obj, dict) else None
                status_obj = fields.get("status") or {}
                current_status = status_obj.get("name", "Unknown") if isinstance(status_obj, dict) else "Unknown"
                issue_created_at = _parse_jira_datetime(fields.get("created", ""))
                is_resolved = issue["_resolved"]
                resolved_at_str = fields.get("resolutiondate")
                resolved_at = _parse_jira_datetime(resolved_at_str) if resolved_at_str else None

                # Fetch changelog
                changelog = client.get_issue_changelog(issue_key)
                time.sleep(0.1)  # Be gentle on the API

                # Parse transitions
                transitions = _extract_status_transitions(changelog, issue_created_at)

                # Determine when the current status was entered
                if transitions:
                    status_entered_at = transitions[-1]["transitioned_at"]
                else:
                    # No transitions: ticket has been in its initial status since creation
                    status_entered_at = issue_created_at

                # Upsert ticket_status_cache
                existing = db.query(TicketStatusCache).filter(
                    TicketStatusCache.issue_key == issue_key
                ).first()

                if existing:
                    existing.project_key = project_key
                    existing.summary = summary
                    existing.priority = priority
                    existing.assignee_account_id = assignee_account_id
                    existing.assignee_display_name = assignee_display_name
                    existing.current_status = current_status
                    existing.status_entered_at = status_entered_at
                    existing.issue_created_at = issue_created_at
                    existing.resolved = is_resolved
                    existing.resolved_at = resolved_at
                    existing.last_synced_at = now
                else:
                    cache_entry = TicketStatusCache(
                        id=str(uuid.uuid4()),
                        issue_key=issue_key,
                        project_key=project_key,
                        summary=summary,
                        priority=priority,
                        assignee_account_id=assignee_account_id,
                        assignee_display_name=assignee_display_name,
                        current_status=current_status,
                        status_entered_at=status_entered_at,
                        issue_created_at=issue_created_at,
                        resolved=is_resolved,
                        resolved_at=resolved_at,
                        last_synced_at=now,
                    )
                    db.add(cache_entry)

                # Upsert status transitions
                for t in transitions:
                    existing_t = db.query(TicketStatusHistory).filter(
                        TicketStatusHistory.issue_key == issue_key,
                        TicketStatusHistory.transitioned_at == t["transitioned_at"],
                        TicketStatusHistory.to_status == t["to_status"],
                    ).first()

                    if not existing_t:
                        history_entry = TicketStatusHistory(
                            id=str(uuid.uuid4()),
                            issue_key=issue_key,
                            from_status=t["from_status"],
                            to_status=t["to_status"],
                            transitioned_at=t["transitioned_at"],
                            time_in_from_seconds=t["time_in_from_seconds"],
                        )
                        db.add(history_entry)
                        transitions_upserted += 1
                    else:
                        # Update time_in_from_seconds if it changed
                        if existing_t.time_in_from_seconds != t["time_in_from_seconds"]:
                            existing_t.time_in_from_seconds = t["time_in_from_seconds"]
                            transitions_upserted += 1

                tickets_synced += 1

                # Commit in batches of 50
                if tickets_synced % 50 == 0:
                    db.commit()
                    logger.info(f"[StatusSync] Processed {tickets_synced}/{len(seen_keys)} tickets")

            except Exception as e:
                logger.warning(f"[StatusSync] Failed to process {issue_key}: {e}")
                continue

        db.commit()

        # Log agent run
        run = AgentRun(
            job_name="status_sync",
            status="success",
            tasks_created=tickets_synced,
            tasks_updated=transitions_upserted,
        )
        db.add(run)
        db.commit()

        logger.info(
            f"[StatusSync] Complete: {tickets_synced} tickets synced, "
            f"{transitions_upserted} transitions upserted"
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[StatusSync] Failed: {e}")
        run = AgentRun(
            job_name="status_sync",
            status="error",
            error_message=str(e)[:500],
        )
        db.add(run)
        db.commit()
    finally:
        db.close()
