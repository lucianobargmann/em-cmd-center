"""Stack rank trigger — fetches metrics and runs ranking script."""

import csv
import logging
import subprocess
from datetime import date, datetime
from pathlib import Path

from database import get_db
from models import AgentRun, Task

logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def run_stack_rank(jira_client, team_projects: list[str], script_path: str) -> dict:
    """Fetch per-engineer metrics and run the stack ranking script.

    Args:
        jira_client: Initialized JiraClient.
        team_projects: List of Jira project keys.
        script_path: Path to the user's stack ranking script.

    Returns:
        Dict with tasks_created count.
    """
    db = get_db()
    created = 0
    today = date.today()
    output_file = OUTPUT_DIR / f"stack_rank_{today.isoformat()}.csv"

    try:
        OUTPUT_DIR.mkdir(exist_ok=True)

        # Gather per-engineer metrics from Jira
        engineer_metrics: dict[str, dict] = {}
        for project in team_projects:
            try:
                issues = jira_client.get_completed_sprint_data(project)
            except Exception as e:
                logger.warning(f"Failed to fetch sprint data for {project}: {e}")
                continue

            for issue in issues:
                fields = issue.get("fields", {})
                assignee = fields.get("assignee")
                if not assignee:
                    continue
                name = assignee.get("displayName", "Unknown")
                sp = fields.get("customfield_10016") or 0

                if name not in engineer_metrics:
                    engineer_metrics[name] = {
                        "name": name,
                        "tickets_completed": 0,
                        "story_points": 0,
                    }
                engineer_metrics[name]["tickets_completed"] += 1
                engineer_metrics[name]["story_points"] += sp

        # Write metrics CSV as input or output
        if script_path:
            # Write input CSV, run external script
            input_file = OUTPUT_DIR / f"metrics_input_{today.isoformat()}.csv"
            with open(input_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["name", "tickets_completed", "story_points"])
                writer.writeheader()
                for m in engineer_metrics.values():
                    writer.writerow(m)

            try:
                result = subprocess.run(
                    ["python", script_path, str(input_file), str(output_file)],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    logger.error(f"Stack rank script failed: {result.stderr}")
            except Exception as e:
                logger.error(f"Failed to run stack rank script: {e}")
        else:
            # No external script — write basic metrics as the output
            with open(output_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["name", "tickets_completed", "story_points"])
                writer.writeheader()
                sorted_engineers = sorted(
                    engineer_metrics.values(),
                    key=lambda x: x["story_points"],
                    reverse=True,
                )
                for m in sorted_engineers:
                    writer.writerow(m)

        # Create review task
        task = Task(
            title=f"Stack rank ready — review output/stack_rank_{today.isoformat()}.csv",
            priority="p3",
            category="reports",
            auto=True,
            source="stack_rank",
        )
        db.add(task)
        created += 1
        db.commit()

        run = AgentRun(
            job_name="stack_rank",
            status="success",
            tasks_created=created,
            tasks_updated=0,
        )
        db.add(run)
        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Stack rank failed: {e}")
        run = AgentRun(
            job_name="stack_rank",
            status="error",
            error_message=str(e),
        )
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()

    return {"tasks_created": created}
