"""Slack DM reminders for developers with unestimated tickets."""

import logging
from datetime import datetime
from urllib.parse import quote

from agent.jira_client import JiraClient
from agent.slack_client import SlackClient
from database import get_db
from models import AgentRun, DeveloperRoster

logger = logging.getLogger(__name__)


def send_sp_reminders(config: dict) -> dict:
    """Send Slack DMs to developers who have tickets without story points.

    Args:
        config: Application configuration dict.

    Returns:
        Summary dict with sent, skipped, errors counts.
    """
    bot_token = config.get("SLACK_BOT_TOKEN", "")
    if not bot_token:
        logger.warning("SLACK_BOT_TOKEN not set, skipping SP reminders")
        return {"sent": 0, "skipped": 0, "errors": ["SLACK_BOT_TOKEN not configured"]}

    slack = SlackClient(bot_token)
    jira = JiraClient(
        config["JIRA_BASE_URL"],
        config["JIRA_EMAIL"],
        config["JIRA_API_TOKEN"],
    )
    jira_base = config["JIRA_BASE_URL"].rstrip("/")
    projects = config.get("JIRA_TEAM_PROJECTS", [])
    proj_str = ", ".join(projects)

    db = get_db()
    try:
        # Load active roster developers with slack_user_id set
        devs = (
            db.query(DeveloperRoster)
            .filter(
                DeveloperRoster.active == True,
                DeveloperRoster.slack_user_id != None,
                DeveloperRoster.slack_user_id != "",
                DeveloperRoster.jira_account_id != None,
                DeveloperRoster.jira_account_id != "",
            )
            .all()
        )
    finally:
        db.close()

    if not devs:
        logger.info("No developers with both Slack and Jira IDs configured")
        return {"sent": 0, "skipped": 0, "errors": []}

    sent = 0
    skipped = 0
    errors: list[str] = []
    notification_log: list[dict] = []

    for dev in devs:
        try:
            # Build JQL for this developer's unestimated tickets
            jql = (
                f'project in ({proj_str}) AND assignee = "{dev.jira_account_id}" '
                f'AND resolution = Unresolved '
                f'AND "Story Points" is EMPTY AND cf[10036] is EMPTY '
                f'AND type not in (Epic, Sub-task)'
            )
            issues = jira.search_issues(jql, ["summary"], max_results=100)

            if not issues:
                skipped += 1
                continue

            count = len(issues)
            first_name = dev.display_name.split()[0] if dev.display_name else "Hey"

            # Build Jira JQL link
            jira_url = f"{jira_base}/issues/?jql={quote(jql)}"

            # Build ticket list (max 10 shown)
            ticket_lines = []
            for issue in issues[:10]:
                key = issue.get("key", "")
                summary = issue.get("fields", {}).get("summary", "")
                ticket_lines.append(f"  \u2022 <{jira_base}/browse/{key}|{key}> — {summary}")
            if count > 10:
                ticket_lines.append(f"  _...and {count - 10} more_")

            text = (
                f"Hey {first_name}! You have *{count} ticket{'s' if count != 1 else ''}* "
                f"without story points.\n\n"
                + "\n".join(ticket_lines)
                + f"\n\n<{jira_url}|View all in Jira>\n"
                f"Could you add estimates when you get a chance? Thanks!"
            )

            slack.send_dm(dev.slack_user_id, text)
            sent += 1
            notification_log.append({"name": dev.display_name, "count": count})
            logger.info("Sent SP reminder to %s (%d tickets)", dev.display_name, count)

        except Exception as e:
            error_msg = f"{dev.display_name}: {e}"
            errors.append(error_msg)
            logger.error("Failed to send SP reminder to %s: %s", dev.display_name, e)

    # Send summary DM to the EM
    em_slack_id = config.get("SLACK_EM_USER_ID", "")
    if em_slack_id and (sent > 0 or errors):
        try:
            summary_lines = [f"*SP Reminder Summary* — {datetime.now().strftime('%b %d, %H:%M')}"]
            summary_lines.append(f"Sent: *{sent}* | Skipped: *{skipped}* (no unestimated tickets)")

            if notification_log:
                summary_lines.append("")
                for entry in notification_log:
                    summary_lines.append(
                        f"  \u2022 *{entry['name']}* — {entry['count']} ticket{'s' if entry['count'] != 1 else ''}"
                    )

            if errors:
                summary_lines.append("")
                summary_lines.append(f"_Errors ({len(errors)}):_")
                for err in errors:
                    summary_lines.append(f"  \u26A0 {err}")

            slack.send_dm(em_slack_id, "\n".join(summary_lines))
            logger.info("Sent SP reminder summary to EM")
        except Exception as e:
            logger.error("Failed to send summary DM to EM: %s", e)

    # Log agent run
    db = get_db()
    try:
        run = AgentRun(
            job_name="slack_sp_reminder",
            status="success" if not errors else "partial",
            tasks_created=sent,
            tasks_updated=0,
            error_message="; ".join(errors) if errors else None,
        )
        db.add(run)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    summary = {"sent": sent, "skipped": skipped, "errors": errors}
    logger.info("SP reminders complete: %s", summary)
    return summary
