"""Infrastructure cost monitoring — detects week-over-week spikes."""

import logging
from datetime import datetime

from database import get_db
from models import AgentRun, Task

logger = logging.getLogger(__name__)


def _fetch_aws_costs() -> dict[str, dict]:
    """Fetch AWS cost data via Cost Explorer.

    Returns:
        Dict mapping service name to {this_week, last_week} cost.
    """
    try:
        import boto3
        from datetime import date, timedelta

        client = boto3.client("ce")
        today = date.today()
        this_week_start = (today - timedelta(days=7)).isoformat()
        last_week_start = (today - timedelta(days=14)).isoformat()
        this_week_end = today.isoformat()
        last_week_end = this_week_start

        costs: dict[str, dict] = {}

        for label, start, end in [
            ("this_week", this_week_start, this_week_end),
            ("last_week", last_week_start, last_week_end),
        ]:
            resp = client.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="DAILY",
                Metrics=["BlendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            for result in resp.get("ResultsByTime", []):
                for group in result.get("Groups", []):
                    service = group["Keys"][0]
                    amount = float(group["Metrics"]["BlendedCost"]["Amount"])
                    if service not in costs:
                        costs[service] = {"this_week": 0.0, "last_week": 0.0}
                    costs[service][label] += amount

        return costs
    except ImportError:
        logger.warning("boto3 not installed — skipping AWS cost check")
        return {}
    except Exception as e:
        logger.error(f"AWS cost fetch failed: {e}")
        return {}


def run_infra_cost_check(cloud_provider: str) -> dict:
    """Check for infrastructure cost spikes and create alert tasks.

    Args:
        cloud_provider: "aws" or "gcp" (only aws implemented).

    Returns:
        Dict with tasks_created count.
    """
    db = get_db()
    created = 0

    try:
        costs: dict[str, dict] = {}

        if cloud_provider.lower() == "aws":
            costs = _fetch_aws_costs()
        else:
            logger.info(f"Cloud provider '{cloud_provider}' not supported or not configured")

        for service, data in costs.items():
            last_week = data.get("last_week", 0)
            this_week = data.get("this_week", 0)

            if last_week > 0:
                pct_change = ((this_week - last_week) / last_week) * 100
            elif this_week > 0:
                pct_change = 100.0
            else:
                continue

            if pct_change > 20:
                title = f"Infra cost spike: {service} +{pct_change:.0f}% vs last week"
                # Check dedup
                existing = (
                    db.query(Task)
                    .filter(Task.title == title, Task.done == False)
                    .first()
                )
                if not existing:
                    task = Task(
                        title=title,
                        priority="p3",
                        category="infra",
                        auto=True,
                        source="infra",
                    )
                    db.add(task)
                    created += 1

        db.commit()

        run = AgentRun(
            job_name="infra_costs",
            status="success",
            tasks_created=created,
            tasks_updated=0,
        )
        db.add(run)
        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Infra cost check failed: {e}")
        run = AgentRun(
            job_name="infra_costs",
            status="error",
            error_message=str(e),
        )
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()

    return {"tasks_created": created}
