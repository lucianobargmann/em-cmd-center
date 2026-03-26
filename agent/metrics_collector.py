"""Weekly developer metrics collection orchestrator.

Collects data from Bitbucket + Jira, computes EPS scores,
and writes per-developer snapshots + team summary to the database.
"""

import logging
import math
import re
import unicodedata
from datetime import date, datetime, timedelta

from agent.bitbucket_client import BitbucketClient
from agent.jira_client import JiraClient
from database import get_db
from models import AgentRun, DeveloperRoster, WeeklySnapshot, WeeklyTeamSummary


def _normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    s = unicodedata.normalize("NFD", name.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()

logger = logging.getLogger(__name__)

# Jira status → dashboard bucket mapping
STATUS_BUCKETS: dict[str, str] = {
    "to do": "todo",
    "open": "todo",
    "backlog": "todo",
    "dev in progress": "wip",
    "in progress": "wip",
    "in review": "wip",
    "reopened": "wip",
    "blocked": "wip",
    "qa pending": "qa",
    "qa in progress": "qa",
    "uat": "qa",
    "done": "closed",
    "resolved": "closed",
    "closed": "closed",
    "released": "closed",
    "deployed": "closed",
}


def _get_status_bucket(status_name: str) -> str:
    """Map a Jira status name to a dashboard bucket."""
    return STATUS_BUCKETS.get(status_name.lower().strip(), "todo")


def _complexity_weight(story_points: float | None) -> float:
    """Complexity weight based on story points."""
    sp = story_points or 0
    if sp <= 2:
        return 0.5
    if sp >= 8:
        return 1.5
    return 1.0


def _velocity_multiplier(sp_per_day: float) -> float:
    """Velocity tier multiplier from SP/day."""
    if sp_per_day >= 3.0:
        return 1.3
    if sp_per_day >= 2.0:
        return 1.2
    if sp_per_day >= 1.0:
        return 1.1
    if sp_per_day >= 0.5:
        return 1.0
    if sp_per_day >= 0.25:
        return 0.9
    return 0.8


def _eps_label(score: float) -> str:
    """EPS status label from score."""
    if score >= 35:
        return "Leading"
    if score >= 25:
        return "Steady"
    if score >= 15:
        return "Ramping"
    return "Emerging"


def _percentile(values: list[float], pct: float) -> float | None:
    """Compute a percentile from a sorted list."""
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = (pct / 100) * (len(sorted_vals) - 1)
    lower = int(math.floor(idx))
    upper = int(math.ceil(idx))
    if lower == upper:
        return round(sorted_vals[lower], 2)
    frac = idx - lower
    return round(sorted_vals[lower] * (1 - frac) + sorted_vals[upper] * frac, 2)


def _compute_cycle_times(jira_client: JiraClient, issues: list[dict]) -> dict[str, list[float]]:
    """Compute cycle times and lead times per assignee from resolved issues.

    Cycle time: first "In Progress" → "Done" transition (in days).
    Lead time: created → resolved (in days).

    Returns:
        Dict mapping assignee account_id → list of (cycle_time, lead_time) tuples.
    """
    result: dict[str, list[tuple[float, float]]] = {}

    for issue in issues:
        fields = issue.get("fields", {})
        assignee = fields.get("assignee")
        if not assignee:
            continue
        account_id = assignee.get("accountId", "")

        # Lead time: created → resolved
        created = fields.get("created", "")
        resolved = fields.get("resolutiondate", "")
        if not created or not resolved:
            continue

        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            resolved_dt = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
            lead_days = max((resolved_dt - created_dt).total_seconds() / 86400, 0.1)
        except (ValueError, AttributeError):
            continue

        # Cycle time from changelog
        issue_key = issue.get("key", "")
        cycle_days = lead_days  # fallback to lead time
        try:
            changelog = jira_client.get_issue_changelog(issue_key)
            first_progress = None
            for entry in changelog:
                for item in entry.get("items", []):
                    if item.get("field") == "status":
                        to_str = (item.get("toString") or "").lower()
                        if "progress" in to_str or "review" in to_str:
                            if first_progress is None:
                                ts = entry.get("created", "")
                                try:
                                    first_progress = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                except (ValueError, AttributeError):
                                    pass
            if first_progress:
                cycle_days = max((resolved_dt - first_progress).total_seconds() / 86400, 0.1)
        except Exception:
            pass

        if account_id not in result:
            result[account_id] = []
        result[account_id].append((cycle_days, lead_days))

    # Flatten to separate lists
    output: dict[str, list[float]] = {}
    all_cycle = []
    all_lead = []
    for aid, pairs in result.items():
        output[f"{aid}_cycle"] = [p[0] for p in pairs]
        output[f"{aid}_lead"] = [p[1] for p in pairs]
        all_cycle.extend(output[f"{aid}_cycle"])
        all_lead.extend(output[f"{aid}_lead"])
    # Include team-wide lists (all assignees, not just roster)
    output["_all_cycle"] = all_cycle
    output["_all_lead"] = all_lead
    return output


def _detect_qa_bounces(jira_client: JiraClient, issues: list[dict]) -> dict[str, int]:
    """Count QA bounces (QA → reopened/dev transitions) per assignee."""
    bounces: dict[str, int] = {}

    for issue in issues:
        fields = issue.get("fields", {})
        assignee = fields.get("assignee")
        if not assignee:
            continue
        account_id = assignee.get("accountId", "")
        issue_key = issue.get("key", "")

        try:
            changelog = jira_client.get_issue_changelog(issue_key)
            for entry in changelog:
                for item in entry.get("items", []):
                    if item.get("field") == "status":
                        from_str = (item.get("fromString") or "").lower()
                        to_str = (item.get("toString") or "").lower()
                        if ("qa" in from_str) and ("progress" in to_str or "reopen" in to_str or "todo" in to_str):
                            bounces[account_id] = bounces.get(account_id, 0) + 1
        except Exception:
            pass

    return bounces


def collect_weekly_metrics(config: dict, week_start: date | None = None) -> None:
    """Main orchestrator: collect metrics from Bitbucket + Jira, write to DB.

    Args:
        config: Application configuration dict.
        week_start: Monday of the week to collect. Defaults to previous Monday.
    """
    db = get_db()
    try:
        # Determine week boundaries
        if week_start is None:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            # If today is Monday, collect for previous week
            if today.weekday() == 0:
                week_start = week_start - timedelta(weeks=1)

        week_end = week_start + timedelta(days=7)
        since_dt = datetime(week_start.year, week_start.month, week_start.day)
        until_dt = datetime(week_end.year, week_end.month, week_end.day)
        since_str = week_start.isoformat()
        until_str = week_end.isoformat()

        logger.info(f"Collecting metrics for week {since_str} to {until_str}")

        # 1. Load active developers
        developers = db.query(DeveloperRoster).filter(DeveloperRoster.active == True).all()
        if not developers:
            logger.warning("No active developers in roster, skipping collection")
            return

        # 2. Build identity lookup maps
        email_map: dict[str, DeveloperRoster] = {}
        bb_map: dict[str, DeveloperRoster] = {}
        jira_map: dict[str, DeveloperRoster] = {}
        # name_parts_map: normalized name parts → dev (for fuzzy git author matching)
        name_parts_map: list[tuple[set[str], DeveloperRoster]] = []
        # email_prefix_map: "jleahy" from jleahy@ninjio.com → dev
        email_prefix_map: dict[str, DeveloperRoster] = {}
        for dev in developers:
            if dev.email:
                email_map[dev.email.lower()] = dev
                prefix = dev.email.lower().split("@")[0]
                email_prefix_map[prefix] = dev
            if dev.bitbucket_username:
                bb_map[dev.bitbucket_username.lower()] = dev
            if dev.jira_account_id:
                jira_map[dev.jira_account_id] = dev
            parts = set(_normalize_name(dev.display_name).split())
            if len(parts) >= 2:
                name_parts_map.append((parts, dev))

        def _match_by_name(raw_name: str) -> DeveloperRoster | None:
            """Fuzzy match a git author name to a roster developer."""
            norm = _normalize_name(raw_name)
            if len(norm) < 3:
                return None
            author_parts = set(norm.split())
            best = None
            best_score = 0
            for parts, dev in name_parts_map:
                overlap = len(parts & author_parts)
                if overlap >= 2 and overlap > best_score:
                    best = dev
                    best_score = overlap
            if best:
                return best
            # Single-word git usernames: try substring match against email prefixes
            if len(author_parts) == 1:
                single = list(author_parts)[0]
                for prefix, dev in email_prefix_map.items():
                    if prefix in single or single in prefix:
                        return dev
            return None

        def _match_by_email(email: str) -> DeveloperRoster | None:
            """Try to match a commit email to a roster developer via prefix patterns."""
            if not email:
                return None
            # Strip GitHub noreply pattern: "688358+cnacorrea@users.noreply.github.com" → "cnacorrea"
            prefix = email.split("@")[0]
            if "+" in prefix:
                prefix = prefix.split("+", 1)[1]
            # Strip dots for comparison: "jvm.leahy" → "jvmleahy"
            prefix_clean = prefix.replace(".", "")
            for ep, dev in email_prefix_map.items():
                ep_clean = ep.replace(".", "")
                if ep_clean in prefix_clean or prefix_clean in ep_clean:
                    return dev
                # Also try: last name from roster matches in prefix
                name_parts = _normalize_name(dev.display_name).split()
                if len(name_parts) >= 2:
                    last = name_parts[-1]
                    if len(last) >= 4 and last in prefix_clean:
                        return dev
            return None

        # Per-developer accumulators
        dev_data: dict[str, dict] = {}
        for dev in developers:
            dev_data[dev.id] = {
                "lines": 0, "prs": 0,
                "tickets": {"todo": 0, "wip": 0, "qa": 0, "closed": 0},
                "sp": {"todo": 0, "wip": 0, "qa": 0, "closed": 0},
            }

        # 3. Bitbucket collection
        bb_workspace = config.get("BITBUCKET_WORKSPACE", "")
        bb_token = config.get("BITBUCKET_API_TOKEN", "")
        if bb_workspace and bb_token:
            try:
                bb_username = config.get("BITBUCKET_USERNAME", "")
                bb = BitbucketClient(bb_workspace, bb_token, username=bb_username)
                repos = bb.get_repos()
                logger.info(f"Found {len(repos)} Bitbucket repos")

                for repo in repos:
                    slug = repo.get("slug", "")
                    if not slug:
                        continue

                    # Commits
                    try:
                        commits = bb.get_commits_in_range(slug, since_dt, until_dt)
                        for commit in commits:
                            author = commit.get("author", {})
                            author_raw = author.get("raw", "")
                            # Extract email from "Name <email>" format
                            email = ""
                            if "<" in author_raw and ">" in author_raw:
                                email = author_raw.split("<")[1].split(">")[0].lower()

                            dev = email_map.get(email)
                            if not dev:
                                # Try matching by BB user display name
                                user = author.get("user", {})
                                bb_name = (user.get("display_name") or user.get("nickname") or "").lower()
                                dev = bb_map.get(bb_name)
                            if not dev:
                                # Try fuzzy name match from git author raw string
                                author_name = author_raw.split("<")[0].strip() if "<" in author_raw else author_raw
                                dev = _match_by_name(author_name)
                            if not dev:
                                # Try email prefix/pattern matching (GitHub noreply, personal emails)
                                dev = _match_by_email(email)
                            if not dev:
                                continue

                            lines = bb.get_diffstat(slug, commit.get("hash", ""))
                            dev_data[dev.id]["lines"] += lines
                    except Exception as e:
                        logger.warning(f"Failed to get commits for {slug}: {e}")

                    # Merged PRs
                    try:
                        prs = bb.get_merged_prs_in_range(slug, since_dt)
                        for pr in prs:
                            pr_author = pr.get("author", {})
                            pr_email = ""
                            # Try user link email
                            pr_user = pr_author if isinstance(pr_author, dict) else {}
                            pr_name = (pr_user.get("display_name") or pr_user.get("nickname") or "").lower()

                            dev = bb_map.get(pr_name)
                            if not dev and pr_name:
                                dev = _match_by_name(pr_name)
                            if dev:
                                dev_data[dev.id]["prs"] += 1
                    except Exception as e:
                        logger.warning(f"Failed to get PRs for {slug}: {e}")

            except Exception as e:
                logger.error(f"Bitbucket collection failed: {e}")
        else:
            logger.info("Bitbucket not configured, skipping")

        # 4. Jira collection
        jira = JiraClient(
            config["JIRA_BASE_URL"],
            config["JIRA_EMAIL"],
            config["JIRA_API_TOKEN"],
        )
        projects = config["JIRA_TEAM_PROJECTS"]
        account_ids = [dev.jira_account_id for dev in developers if dev.jira_account_id]

        # Resolved tickets for this specific week (date-filtered)
        resolved_issues = []
        try:
            resolved_issues = jira.get_resolved_in_range(projects, since_str, until_str)
            logger.info(f"Found {len(resolved_issues)} resolved issues for week {since_str}")
        except Exception as e:
            logger.error(f"Failed to get resolved issues: {e}")

        # Derive closed ticket/SP counts from resolved issues (week-specific)
        for issue in resolved_issues:
            fields = issue.get("fields", {})
            assignee = fields.get("assignee")
            if not assignee:
                continue
            aid = assignee.get("accountId", "")
            dev = jira_map.get(aid)
            if not dev:
                continue
            sp = fields.get("customfield_10016") or 0
            dev_data[dev.id]["tickets"]["closed"] += 1
            dev_data[dev.id]["sp"]["closed"] += int(sp)

        # Active (non-resolved) tickets snapshot — only meaningful for current/recent week
        if account_ids:
            try:
                all_active = jira.get_tickets_by_assignees_active(projects, account_ids)
                for issue in all_active:
                    fields = issue.get("fields", {})
                    assignee = fields.get("assignee")
                    if not assignee:
                        continue
                    aid = assignee.get("accountId", "")
                    dev = jira_map.get(aid)
                    if not dev:
                        continue
                    status_name = (fields.get("status") or {}).get("name", "")
                    bucket = _get_status_bucket(status_name)
                    if bucket == "closed":
                        continue  # already counted from resolved_issues
                    sp = fields.get("customfield_10016") or 0
                    dev_data[dev.id]["tickets"][bucket] += 1
                    dev_data[dev.id]["sp"][bucket] += int(sp)
            except Exception as e:
                logger.error(f"Failed to get active tickets: {e}")

        # Cycle/lead times
        time_data = _compute_cycle_times(jira, resolved_issues)

        # QA bounces
        bounces = _detect_qa_bounces(jira, resolved_issues)

        # Defects
        defect_data = {"open": [], "new": [], "closed": []}
        try:
            defect_data = jira.get_defects(projects, created_since=since_str, resolved_since=since_str)
        except Exception as e:
            logger.error(f"Failed to get defects: {e}")

        # Classify defects by priority field (Highest/High=P1, Medium=P2, rest=other)
        defects_p1 = 0
        defects_p2 = 0
        defects_other = 0
        for bug in defect_data["open"]:
            priority_name = (bug.get("fields", {}).get("priority") or {}).get("name", "").lower()
            if priority_name in ("highest", "high", "critical", "blocker"):
                defects_p1 += 1
            elif priority_name in ("medium",):
                defects_p2 += 1
            else:
                defects_other += 1

        # 5. Compute EPS and write snapshots
        # Delete existing snapshots for this week (upsert)
        db.query(WeeklySnapshot).filter(WeeklySnapshot.week_start == week_start).delete()

        roster_cycle_times: list[float] = []
        roster_lead_times: list[float] = []

        for dev in developers:
            dd = dev_data[dev.id]
            aid = dev.jira_account_id or ""

            # Cycle/lead time stats
            cycle_times = time_data.get(f"{aid}_cycle", [])
            lead_times = time_data.get(f"{aid}_lead", [])
            roster_cycle_times.extend(cycle_times)
            roster_lead_times.extend(lead_times)

            ct_mean = round(sum(cycle_times) / len(cycle_times), 2) if cycle_times else None
            ct_median = _percentile(cycle_times, 50)
            ct_p85 = _percentile(cycle_times, 85)
            lt_mean = round(sum(lead_times) / len(lead_times), 2) if lead_times else None
            lt_median = _percentile(lead_times, 50)
            lt_p85 = _percentile(lead_times, 85)

            # EPS: PS
            # Count closed SP from resolved issues for this dev
            dev_closed_sp = 0
            dev_resolved_count = 0
            ps = 0
            for issue in resolved_issues:
                issue_assignee = issue.get("fields", {}).get("assignee")
                if not issue_assignee or issue_assignee.get("accountId") != aid:
                    continue
                sp = issue.get("fields", {}).get("customfield_10016") or 0
                dev_closed_sp += sp
                dev_resolved_count += 1
                ps += sp * _complexity_weight(sp)

            # QM
            bounce_count = bounces.get(aid, 0)
            bounce_rate = bounce_count / max(dev_resolved_count, 1)
            qm = max(1 - bounce_rate, 0.3)

            # VM
            if cycle_times:
                avg_cycle = sum(cycle_times) / len(cycle_times)
                sp_per_day = dev_closed_sp / max(avg_cycle, 0.1)
            else:
                sp_per_day = 0
            vm = _velocity_multiplier(sp_per_day)

            eps_score = round(ps * qm * vm, 1)

            snapshot = WeeklySnapshot(
                week_start=week_start,
                developer_id=dev.id,
                lines_committed=dd["lines"],
                pr_count=dd["prs"],
                tickets_todo=dd["tickets"]["todo"],
                tickets_wip=dd["tickets"]["wip"],
                tickets_qa=dd["tickets"]["qa"],
                tickets_closed=dd["tickets"]["closed"],
                sp_todo=dd["sp"]["todo"],
                sp_wip=dd["sp"]["wip"],
                sp_qa=dd["sp"]["qa"],
                sp_closed=dd["sp"]["closed"],
                cycle_time_mean=ct_mean,
                cycle_time_median=ct_median,
                cycle_time_p85=ct_p85,
                lead_time_mean=lt_mean,
                lead_time_median=lt_median,
                lead_time_p85=lt_p85,
                defects_total=len(defect_data["open"]),
                defects_new=len(defect_data["new"]),
                defects_closed=len(defect_data["closed"]),
                defects_p1=defects_p1,
                defects_p2=defects_p2,
                defects_other=defects_other,
                eps_productivity=round(ps, 1),
                eps_quality=round(qm, 2),
                eps_velocity=round(vm, 1),
                eps_score=eps_score,
            )
            db.add(snapshot)

        # 6. Write team summary
        db.query(WeeklyTeamSummary).filter(WeeklyTeamSummary.week_start == week_start).delete()

        # Use ALL resolved issues for official cycle/lead time (not just roster devs)
        team_cycle = time_data.get("_all_cycle", [])
        team_lead = time_data.get("_all_lead", [])
        avg_ct = round(sum(team_cycle) / len(team_cycle), 2) if team_cycle else None
        avg_lt = round(sum(team_lead) / len(team_lead), 2) if team_lead else None
        med_ct = _percentile(team_cycle, 50)
        med_lt = _percentile(team_lead, 50)

        total_lines = sum(dd["lines"] for dd in dev_data.values())
        total_prs = sum(dd["prs"] for dd in dev_data.values())
        total_tickets_closed = sum(dd["tickets"]["closed"] for dd in dev_data.values())
        total_sp_closed = sum(dd["sp"]["closed"] for dd in dev_data.values())

        # Roster-only averages
        roster_avg_ct = round(sum(roster_cycle_times) / len(roster_cycle_times), 2) if roster_cycle_times else None
        roster_avg_lt = round(sum(roster_lead_times) / len(roster_lead_times), 2) if roster_lead_times else None

        summary = WeeklyTeamSummary(
            week_start=week_start,
            total_lines=total_lines,
            total_prs=total_prs,
            total_tickets_closed=total_tickets_closed,
            total_sp_closed=total_sp_closed,
            avg_cycle_time=avg_ct,
            avg_lead_time=avg_lt,
            avg_cycle_time_median=med_ct,
            avg_lead_time_median=med_lt,
            all_issues_count=len(team_cycle),
            roster_avg_cycle_time=roster_avg_ct,
            roster_avg_lead_time=roster_avg_lt,
            roster_issues_count=len(roster_cycle_times),
            defects_total=len(defect_data["open"]),
            defects_new=len(defect_data["new"]),
            defects_closed=len(defect_data["closed"]),
            defects_p1=defects_p1,
            defects_p2=defects_p2,
            defects_other=defects_other,
        )
        db.add(summary)

        # 7. Cleanup: delete snapshots older than 12 weeks
        cutoff = week_start - timedelta(weeks=12)
        db.query(WeeklySnapshot).filter(WeeklySnapshot.week_start < cutoff).delete()
        db.query(WeeklyTeamSummary).filter(WeeklyTeamSummary.week_start < cutoff).delete()

        # 8. Log AgentRun
        run = AgentRun(
            job_name="weekly_metrics",
            status="success",
            tasks_created=len(developers),
        )
        db.add(run)
        db.commit()

        logger.info(f"Metrics collection complete for {since_str}: {len(developers)} developers")

    except Exception as e:
        db.rollback()
        logger.error(f"Metrics collection failed: {e}")
        run = AgentRun(job_name="weekly_metrics", status="error", error_message=str(e))
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()
