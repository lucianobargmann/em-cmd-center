"""Developer Metrics dashboard REST API endpoints."""

import logging
import threading
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_db
from models import DeveloperRoster, WeeklySnapshot, WeeklyTeamSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

_config: dict = {}

# Manual metrics run tracking
_metrics_runs: dict[str, dict] = {}
_metrics_runs_lock = threading.Lock()


def set_metrics_config(config: dict) -> None:
    """Store config reference for metrics collection."""
    global _config
    _config = config


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ---- Pydantic models ----

class DeveloperCreate(BaseModel):
    display_name: str
    email: str
    jira_account_id: str | None = None
    bitbucket_username: str | None = None
    team: str = "engineering"
    role: str = "Engineer"
    start_date: str | None = None


class DeveloperUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    jira_account_id: str | None = None
    bitbucket_username: str | None = None
    slack_user_id: str | None = None
    team: str | None = None
    role: str | None = None
    start_date: str | None = None
    active: bool | None = None


# ---- Dashboard ----

@router.get("/dashboard")
def get_dashboard(week_start: str | None = None) -> dict:
    """Full dashboard data for a given week."""
    db = get_db()
    try:
        if week_start:
            ws = date.fromisoformat(week_start)
        else:
            ws = _monday_of_week(date.today())

        prev_ws = ws - timedelta(weeks=1)

        # Team summary
        summary = db.query(WeeklyTeamSummary).filter(WeeklyTeamSummary.week_start == ws).first()
        prev_summary = db.query(WeeklyTeamSummary).filter(WeeklyTeamSummary.week_start == prev_ws).first()

        official_metrics = {
            # All tickets (team-wide, includes non-roster assignees)
            "avg_cycle_time": summary.avg_cycle_time if summary else None,
            "avg_lead_time": summary.avg_lead_time if summary else None,
            "all_issues_count": summary.all_issues_count if summary else 0,
            "prev_cycle_time": prev_summary.avg_cycle_time if prev_summary else None,
            "prev_lead_time": prev_summary.avg_lead_time if prev_summary else None,
            # Roster developers only
            "roster_avg_cycle_time": summary.roster_avg_cycle_time if summary else None,
            "roster_avg_lead_time": summary.roster_avg_lead_time if summary else None,
            "roster_issues_count": summary.roster_issues_count if summary else 0,
            "prev_roster_cycle_time": prev_summary.roster_avg_cycle_time if prev_summary else None,
            "prev_roster_lead_time": prev_summary.roster_avg_lead_time if prev_summary else None,
        }

        # Developer snapshots
        snapshots = db.query(WeeklySnapshot).filter(WeeklySnapshot.week_start == ws).all()
        prev_snapshots = db.query(WeeklySnapshot).filter(WeeklySnapshot.week_start == prev_ws).all()
        prev_map = {s.developer_id: s for s in prev_snapshots}

        # Developer names
        dev_ids = [s.developer_id for s in snapshots]
        devs = db.query(DeveloperRoster).filter(DeveloperRoster.id.in_(dev_ids)).all() if dev_ids else []
        dev_name_map = {d.id: d.display_name for d in devs}

        developers_list = []
        for s in snapshots:
            prev = prev_map.get(s.developer_id)
            wow_ct_delta = None
            if s.cycle_time_mean is not None and prev and prev.cycle_time_mean is not None:
                wow_ct_delta = round(s.cycle_time_mean - prev.cycle_time_mean, 2)

            sp_closed = s.sp_closed or 0
            ct = s.cycle_time_mean or 0
            sp_per_day = round(sp_closed / max(ct, 0.1), 2) if sp_closed else 0

            eps_data = s.to_dict()["eps"]
            eps_data["label"] = _eps_label(eps_data.get("score") or 0)

            developers_list.append({
                "id": s.developer_id,
                "name": dev_name_map.get(s.developer_id, "Unknown"),
                "lines_committed": s.lines_committed,
                "pr_count": s.pr_count,
                "tickets": s.to_dict()["tickets"],
                "story_points": s.to_dict()["story_points"],
                "cycle_time": s.to_dict()["cycle_time"],
                "lead_time": s.to_dict()["lead_time"],
                "sp_per_day": sp_per_day,
                "eps": eps_data,
                "wow_cycle_time_delta": wow_ct_delta,
            })

        # Defects (from team summary)
        jira_base = _config.get("JIRA_BASE_URL", "")
        projects = _config.get("JIRA_TEAM_PROJECTS", [])
        proj_jql = ", ".join(projects) if projects else ""
        defects = {"total": 0, "new": 0, "closed": 0, "p1": 0, "p2": 0, "other": 0,
                   "trend": "neutral", "wow_delta": 0, "four_week_avg": 0,
                   "jira_open_url": f"{jira_base}/issues/?jql=project in ({proj_jql}) AND type = Bug AND resolution = Unresolved" if jira_base else "",
                   "jira_new_url": f"{jira_base}/issues/?jql=project in ({proj_jql}) AND type = Bug AND created >= \"{ws.isoformat()}\"" if jira_base else "",
                   "jira_closed_url": f"{jira_base}/issues/?jql=project in ({proj_jql}) AND type = Bug AND resolved >= \"{ws.isoformat()}\"" if jira_base else "",
                   }
        if summary:
            defects["total"] = summary.defects_total
            defects["new"] = summary.defects_new
            defects["closed"] = summary.defects_closed
            defects["p1"] = summary.defects_p1
            defects["p2"] = summary.defects_p2
            defects["other"] = summary.defects_other

            if prev_summary:
                delta = summary.defects_new - prev_summary.defects_new
                defects["wow_delta"] = delta
                defects["trend"] = "up" if delta > 0 else ("down" if delta < 0 else "neutral")

            # 4-week rolling average
            four_weeks_ago = ws - timedelta(weeks=3)
            recent = (
                db.query(WeeklyTeamSummary)
                .filter(WeeklyTeamSummary.week_start >= four_weeks_ago, WeeklyTeamSummary.week_start <= ws)
                .all()
            )
            if recent:
                defects["four_week_avg"] = round(sum(r.defects_new for r in recent) / len(recent), 1)

        # Defect history (up to 12 weeks)
        all_summaries = (
            db.query(WeeklyTeamSummary)
            .order_by(WeeklyTeamSummary.week_start.asc())
            .all()
        )
        defect_history = [
            {
                "week_start": s.week_start.isoformat(),
                "p1": s.defects_p1,
                "p2": s.defects_p2,
                "other": s.defects_other,
            }
            for s in all_summaries
        ]

        # Available weeks
        weeks_available = sorted(
            set(s.week_start.isoformat() for s in all_summaries),
            reverse=True,
        )

        return {
            "week_start": ws.isoformat(),
            "official_metrics": official_metrics,
            "developers": developers_list,
            "defects": defects,
            "defect_history": defect_history,
            "weeks_available": weeks_available,
        }
    finally:
        db.close()


def _eps_label(score: float) -> str:
    if score >= 35:
        return "Leading"
    if score >= 25:
        return "Steady"
    if score >= 15:
        return "Ramping"
    return "Emerging"


# ---- Report ----

@router.get("/report")
def get_report(week_start: str | None = None) -> dict:
    """Generate a copyable text report for the week."""
    db = get_db()
    try:
        if week_start:
            ws = date.fromisoformat(week_start)
        else:
            ws = _monday_of_week(date.today())

        summary = db.query(WeeklyTeamSummary).filter(WeeklyTeamSummary.week_start == ws).first()
        snapshots = db.query(WeeklySnapshot).filter(WeeklySnapshot.week_start == ws).all()

        dev_ids = [s.developer_id for s in snapshots]
        devs = db.query(DeveloperRoster).filter(DeveloperRoster.id.in_(dev_ids)).all() if dev_ids else []
        dev_name_map = {d.id: d.display_name for d in devs}

        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        week_label = f"{months[ws.month - 1]} {ws.day}"

        lines = []
        lines.append(f"Developer Metrics Report — Week of {week_label}")
        lines.append("=" * 50)
        lines.append("")

        if summary:
            lines.append("Official Metrics")
            lines.append(f"  Avg Cycle Time: {summary.avg_cycle_time or 'N/A'} days")
            lines.append(f"  Avg Lead Time:  {summary.avg_lead_time or 'N/A'} days")
            lines.append("")
            lines.append("Team Totals")
            lines.append(f"  Lines: {summary.total_lines} | PRs: {summary.total_prs}")
            lines.append(f"  Tickets Closed: {summary.total_tickets_closed} | SP Closed: {summary.total_sp_closed}")
            lines.append("")
            lines.append("Defects")
            lines.append(f"  Open: {summary.defects_total} (P1: {summary.defects_p1}, P2: {summary.defects_p2}, Other: {summary.defects_other})")
            lines.append(f"  New: {summary.defects_new} | Closed: {summary.defects_closed}")
            lines.append("")

        if snapshots:
            lines.append("Per-Developer Breakdown")
            lines.append("-" * 50)
            for s in sorted(snapshots, key=lambda x: (x.eps_score or 0), reverse=True):
                name = dev_name_map.get(s.developer_id, "Unknown")
                lines.append(f"  {name}")
                lines.append(f"    Lines: {s.lines_committed} | PRs: {s.pr_count}")
                lines.append(f"    Tickets: TODO={s.tickets_todo} WIP={s.tickets_wip} QA={s.tickets_qa} Done={s.tickets_closed}")
                lines.append(f"    SP: TODO={s.sp_todo} WIP={s.sp_wip} QA={s.sp_qa} Done={s.sp_closed}")
                if s.cycle_time_mean is not None:
                    lines.append(f"    Cycle Time: {s.cycle_time_mean}d (median {s.cycle_time_median}d, p85 {s.cycle_time_p85}d)")
                if s.lead_time_mean is not None:
                    lines.append(f"    Lead Time:  {s.lead_time_mean}d (median {s.lead_time_median}d, p85 {s.lead_time_p85}d)")
                if s.eps_score is not None:
                    label = _eps_label(s.eps_score)
                    lines.append(f"    EPS: {s.eps_score} ({label}) — PS={s.eps_productivity} QM={s.eps_quality} VM={s.eps_velocity}")
                lines.append("")
        else:
            lines.append("No data for this week.")

        return {"text": "\n".join(lines), "week_start": ws.isoformat()}
    finally:
        db.close()


# ---- Developer Roster ----

@router.get("/developers")
def list_developers() -> list[dict]:
    db = get_db()
    try:
        devs = db.query(DeveloperRoster).filter(DeveloperRoster.active == True).order_by(DeveloperRoster.display_name).all()
        return [d.to_dict() for d in devs]
    finally:
        db.close()


@router.post("/developers", status_code=201)
def add_developer(body: DeveloperCreate) -> dict:
    db = get_db()
    try:
        existing = db.query(DeveloperRoster).filter(DeveloperRoster.email == body.email).first()
        if existing:
            raise HTTPException(status_code=409, detail="Developer with this email already exists")

        dev = DeveloperRoster(
            display_name=body.display_name,
            email=body.email,
            jira_account_id=body.jira_account_id,
            bitbucket_username=body.bitbucket_username,
            team=body.team,
            role=body.role,
            start_date=date.fromisoformat(body.start_date) if body.start_date else None,
        )
        db.add(dev)
        db.commit()
        db.refresh(dev)
        return dev.to_dict()
    finally:
        db.close()


@router.patch("/developers/{dev_id}")
def update_developer(dev_id: str, body: DeveloperUpdate) -> dict:
    db = get_db()
    try:
        dev = db.query(DeveloperRoster).filter(DeveloperRoster.id == dev_id).first()
        if not dev:
            raise HTTPException(status_code=404, detail="Developer not found")

        update_data = body.model_dump(exclude_unset=True)
        if "start_date" in update_data and update_data["start_date"]:
            update_data["start_date"] = date.fromisoformat(update_data["start_date"])

        for key, value in update_data.items():
            setattr(dev, key, value)
        dev.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(dev)
        return dev.to_dict()
    finally:
        db.close()


@router.delete("/developers/{dev_id}")
def remove_developer(dev_id: str) -> dict:
    db = get_db()
    try:
        dev = db.query(DeveloperRoster).filter(DeveloperRoster.id == dev_id).first()
        if not dev:
            raise HTTPException(status_code=404, detail="Developer not found")
        dev.active = False
        dev.updated_at = datetime.utcnow()
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ---- Merge Developers ----

class MergeRequest(BaseModel):
    keep_id: str
    merge_ids: list[str]


@router.post("/developers/merge")
def merge_developers(body: MergeRequest) -> dict:
    """Merge multiple roster entries into one. Keeps keep_id, deactivates merge_ids.

    Reassigns all WeeklySnapshot records from merge_ids to keep_id.
    Fills in any empty identity fields on the keeper from merged entries.
    """
    db = get_db()
    try:
        keeper = db.query(DeveloperRoster).filter(DeveloperRoster.id == body.keep_id).first()
        if not keeper:
            raise HTTPException(status_code=404, detail="Target developer not found")

        merged_count = 0
        for mid in body.merge_ids:
            if mid == body.keep_id:
                continue
            other = db.query(DeveloperRoster).filter(DeveloperRoster.id == mid).first()
            if not other:
                continue

            # Fill empty identity fields from the merged developer
            if not keeper.jira_account_id and other.jira_account_id:
                keeper.jira_account_id = other.jira_account_id
            if not keeper.bitbucket_username and other.bitbucket_username:
                keeper.bitbucket_username = other.bitbucket_username
            if not keeper.email and other.email:
                keeper.email = other.email

            # Reassign snapshots
            db.query(WeeklySnapshot).filter(
                WeeklySnapshot.developer_id == mid
            ).update({WeeklySnapshot.developer_id: body.keep_id})

            # Deactivate merged entry
            other.active = False
            other.updated_at = datetime.utcnow()
            merged_count += 1

        keeper.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(keeper)
        return {"ok": True, "merged": merged_count, "developer": keeper.to_dict()}
    finally:
        db.close()


# ---- External User Lookups ----

@router.get("/jira-users")
def list_jira_users(query: str = "") -> list[dict]:
    """Search Jira users to help populate roster Jira Account IDs."""
    if not _config.get("JIRA_BASE_URL"):
        return []
    from agent.jira_client import JiraClient
    client = JiraClient(
        _config["JIRA_BASE_URL"], _config["JIRA_EMAIL"], _config["JIRA_API_TOKEN"]
    )
    try:
        resp = client._request("GET", "/rest/api/3/user/search", params={
            "query": query, "maxResults": 50
        })
        users = resp.json()
        return [
            {
                "accountId": u.get("accountId", ""),
                "displayName": u.get("displayName", ""),
                "emailAddress": u.get("emailAddress", ""),
                "active": u.get("active", False),
            }
            for u in users
            if u.get("accountType") == "atlassian"
        ]
    except Exception as e:
        logger.warning(f"Failed to list Jira users: {e}")
        return []


@router.get("/bitbucket-users")
def list_bitbucket_users() -> list[dict]:
    """List Bitbucket workspace members to help populate roster BB usernames."""
    workspace = _config.get("BITBUCKET_WORKSPACE", "")
    token = _config.get("BITBUCKET_API_TOKEN", "")
    if not workspace or not token:
        return []
    from agent.bitbucket_client import BitbucketClient
    username = _config.get("BITBUCKET_USERNAME", "")
    bb = BitbucketClient(workspace, token, username=username)
    try:
        url = f"https://api.bitbucket.org/2.0/workspaces/{workspace}/members"
        members = bb._paginate(url, params={"pagelen": 100})
        return [
            {
                "display_name": m.get("user", {}).get("display_name", ""),
                "nickname": m.get("user", {}).get("nickname", ""),
                "uuid": m.get("user", {}).get("uuid", ""),
            }
            for m in members
        ]
    except Exception as e:
        logger.warning(f"Failed to list Bitbucket users: {e}")
        return []


# ---- Bitbucket Auto-Match ----

import unicodedata
import re


def _normalize(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    s = unicodedata.normalize("NFD", name.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def _name_parts(name: str) -> set[str]:
    return set(_normalize(name).split())


def _match_bb_user(display_name: str, bb_users: list[dict]) -> dict | None:
    """Find best Bitbucket member match for a display name."""
    norm = _normalize(display_name)
    parts = _name_parts(display_name)

    # 1) Exact normalized match
    for u in bb_users:
        if _normalize(u["display_name"]) == norm:
            return u

    # 2) All name parts match (handles reordered names like "Luis Felipe Cordeiro Sena" vs "Luis Sena")
    for u in bb_users:
        bb_parts = _name_parts(u["display_name"])
        if parts and bb_parts and parts.issubset(bb_parts) or bb_parts.issubset(parts):
            if len(parts & bb_parts) >= 2:
                return u

    # 3) First + last name match (at least 2 parts in common)
    if len(parts) >= 2:
        for u in bb_users:
            bb_parts = _name_parts(u["display_name"])
            if len(parts & bb_parts) >= 2:
                return u

    return None


@router.get("/bitbucket-match")
def match_bitbucket_user(name: str = "") -> dict:
    """Find the best Bitbucket user match for a given display name."""
    if not name:
        return {"match": None}

    workspace = _config.get("BITBUCKET_WORKSPACE", "")
    token = _config.get("BITBUCKET_API_TOKEN", "")
    if not workspace or not token:
        return {"match": None}

    from agent.bitbucket_client import BitbucketClient
    username = _config.get("BITBUCKET_USERNAME", "")
    bb = BitbucketClient(workspace, token, username=username)
    try:
        url = f"https://api.bitbucket.org/2.0/workspaces/{workspace}/members"
        members = bb._paginate(url, params={"pagelen": 100})
        bb_users = [
            {
                "display_name": m.get("user", {}).get("display_name", ""),
                "nickname": m.get("user", {}).get("nickname", ""),
                "uuid": m.get("user", {}).get("uuid", ""),
            }
            for m in members
        ]
    except Exception as e:
        logger.warning(f"Failed to list BB users for matching: {e}")
        return {"match": None}

    matched = _match_bb_user(name, bb_users)
    return {"match": matched}


@router.post("/bitbucket-automatch")
def bulk_automatch_bitbucket() -> dict:
    """Auto-match all roster developers missing BB username to Bitbucket members."""
    workspace = _config.get("BITBUCKET_WORKSPACE", "")
    token = _config.get("BITBUCKET_API_TOKEN", "")
    if not workspace or not token:
        return {"matched": 0, "results": []}

    from agent.bitbucket_client import BitbucketClient
    username = _config.get("BITBUCKET_USERNAME", "")
    bb = BitbucketClient(workspace, token, username=username)
    try:
        url = f"https://api.bitbucket.org/2.0/workspaces/{workspace}/members"
        members = bb._paginate(url, params={"pagelen": 100})
        bb_users = [
            {
                "display_name": m.get("user", {}).get("display_name", ""),
                "nickname": m.get("user", {}).get("nickname", ""),
                "uuid": m.get("user", {}).get("uuid", ""),
            }
            for m in members
        ]
    except Exception as e:
        logger.warning(f"Failed to list BB users for bulk match: {e}")
        return {"matched": 0, "results": []}

    db = get_db()
    try:
        devs = (
            db.query(DeveloperRoster)
            .filter(DeveloperRoster.active == True,
                    (DeveloperRoster.bitbucket_username == None) | (DeveloperRoster.bitbucket_username == ""))
            .all()
        )

        results = []
        matched_count = 0
        for dev in devs:
            bb_match = _match_bb_user(dev.display_name, bb_users)
            if bb_match:
                dev.bitbucket_username = bb_match["nickname"]
                dev.updated_at = datetime.utcnow()
                matched_count += 1
                results.append({
                    "developer": dev.display_name,
                    "bb_match": bb_match["display_name"],
                    "bb_nickname": bb_match["nickname"],
                })
            else:
                results.append({
                    "developer": dev.display_name,
                    "bb_match": None,
                    "bb_nickname": None,
                })

        db.commit()
        return {"matched": matched_count, "total": len(devs), "results": results}
    finally:
        db.close()


# ---- Unestimated Tickets ----

@router.get("/unestimated")
def get_unestimated_tickets() -> dict:
    """Fetch open tickets without story points for roster developers, grouped by assignee."""
    if not _config.get("JIRA_BASE_URL"):
        return {"by_assignee": {}, "total": 0, "jira_url": ""}

    db = get_db()
    try:
        # Get roster Jira account IDs
        devs = db.query(DeveloperRoster).filter(
            DeveloperRoster.active == True,
            DeveloperRoster.jira_account_id != None,
            DeveloperRoster.jira_account_id != "",
        ).all()
        if not devs:
            return {"by_assignee": {}, "total": 0, "jira_url": ""}

        account_ids = [d.jira_account_id for d in devs]
        ids_str = ", ".join(f'"{aid}"' for aid in account_ids)
    finally:
        db.close()

    from agent.jira_client import JiraClient
    jira = JiraClient(
        _config["JIRA_BASE_URL"], _config["JIRA_EMAIL"], _config["JIRA_API_TOKEN"]
    )
    projects = _config.get("JIRA_TEAM_PROJECTS", [])
    proj_str = ", ".join(projects)
    jql = (
        f"project in ({proj_str}) AND resolution = Unresolved "
        f"AND assignee in ({ids_str}) "
        f"AND (\"Story Points\" is EMPTY OR \"Story Points\" = 0) "
        f"AND type not in (Epic, Sub-task) ORDER BY assignee ASC, priority DESC"
    )

    try:
        issues = jira.search_issues(jql, ["summary", "assignee", "status", "priority", "issuetype"])
    except Exception as e:
        logger.warning(f"Failed to fetch unestimated tickets: {e}")
        return {"by_assignee": {}, "total": 0, "jira_url": ""}

    # Group by assignee
    by_assignee: dict[str, list] = {}
    for issue in issues:
        fields = issue.get("fields", {})
        assignee = fields.get("assignee")
        name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        by_assignee.setdefault(name, []).append({
            "key": issue.get("key", ""),
            "summary": fields.get("summary", ""),
            "status": (fields.get("status") or {}).get("name", ""),
            "priority": (fields.get("priority") or {}).get("name", ""),
            "type": (fields.get("issuetype") or {}).get("name", ""),
        })

    jira_base = _config.get("JIRA_BASE_URL", "")
    jira_url = f"{jira_base}/issues/?jql={jql}" if jira_base else ""

    return {
        "by_assignee": by_assignee,
        "total": len(issues),
        "jira_url": jira_url,
    }


# ---- Manual Collection ----

@router.post("/collect")
def trigger_collection() -> dict:
    job_id = str(uuid.uuid4())
    with _metrics_runs_lock:
        _metrics_runs[job_id] = {"status": "pending", "started_at": datetime.utcnow().isoformat()}

    def _run():
        with _metrics_runs_lock:
            _metrics_runs[job_id]["status"] = "running"
        try:
            from agent.metrics_collector import collect_weekly_metrics
            collect_weekly_metrics(_config)
            with _metrics_runs_lock:
                _metrics_runs[job_id]["status"] = "done"
        except Exception as e:
            with _metrics_runs_lock:
                _metrics_runs[job_id]["status"] = "error"
                _metrics_runs[job_id]["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"job_id": job_id}


@router.get("/collect/{job_id}")
def get_collection_status(job_id: str) -> dict:
    with _metrics_runs_lock:
        run = _metrics_runs.get(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Job not found")
    return run


# ---- Slack SP Reminders ----

_slack_runs: dict[str, dict] = {}
_slack_runs_lock = threading.Lock()


@router.post("/slack-remind")
def trigger_slack_reminders() -> dict:
    """Manually trigger Slack SP reminder DMs."""
    if not _config.get("SLACK_BOT_TOKEN"):
        raise HTTPException(status_code=400, detail="SLACK_BOT_TOKEN not configured")

    job_id = str(uuid.uuid4())
    with _slack_runs_lock:
        _slack_runs[job_id] = {"status": "pending", "started_at": datetime.utcnow().isoformat()}

    def _run():
        with _slack_runs_lock:
            _slack_runs[job_id]["status"] = "running"
        try:
            from agent.slack_reminders import send_sp_reminders
            result = send_sp_reminders(_config)
            with _slack_runs_lock:
                _slack_runs[job_id]["status"] = "done"
                _slack_runs[job_id]["result"] = result
        except Exception as e:
            with _slack_runs_lock:
                _slack_runs[job_id]["status"] = "error"
                _slack_runs[job_id]["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"job_id": job_id}


@router.get("/slack-remind/{job_id}")
def get_slack_remind_status(job_id: str) -> dict:
    with _slack_runs_lock:
        run = _slack_runs.get(job_id)
    if not run:
        raise HTTPException(status_code=404, detail="Job not found")
    return run


@router.get("/slack-users")
def list_slack_users() -> list[dict]:
    """List Slack workspace users for roster mapping."""
    if not _config.get("SLACK_BOT_TOKEN"):
        return []
    from agent.slack_client import SlackClient
    slack = SlackClient(_config["SLACK_BOT_TOKEN"])
    try:
        return slack.list_users()
    except Exception as e:
        logger.warning(f"Failed to list Slack users: {e}")
        return []


@router.post("/slack-automatch")
def bulk_automatch_slack() -> dict:
    """Auto-match roster developers to Slack users by email."""
    if not _config.get("SLACK_BOT_TOKEN"):
        return {"matched": 0, "results": []}

    from agent.slack_client import SlackClient
    slack = SlackClient(_config["SLACK_BOT_TOKEN"])
    try:
        slack_users = slack.list_users()
    except Exception as e:
        logger.warning(f"Failed to list Slack users for bulk match: {e}")
        return {"matched": 0, "results": []}

    # Build email → Slack user map
    email_map = {}
    for u in slack_users:
        email = u.get("email", "").lower().strip()
        if email:
            email_map[email] = u

    db = get_db()
    try:
        devs = (
            db.query(DeveloperRoster)
            .filter(
                DeveloperRoster.active == True,
                (DeveloperRoster.slack_user_id == None) | (DeveloperRoster.slack_user_id == ""),
            )
            .all()
        )

        results = []
        matched_count = 0
        for dev in devs:
            dev_email = (dev.email or "").lower().strip()
            slack_match = email_map.get(dev_email)
            if slack_match:
                dev.slack_user_id = slack_match["id"]
                dev.updated_at = datetime.utcnow()
                matched_count += 1
                results.append({
                    "developer": dev.display_name,
                    "slack_match": slack_match["real_name"],
                    "slack_id": slack_match["id"],
                })
            else:
                results.append({
                    "developer": dev.display_name,
                    "slack_match": None,
                    "slack_id": None,
                })

        db.commit()
        return {"matched": matched_count, "total": len(devs), "results": results}
    finally:
        db.close()
