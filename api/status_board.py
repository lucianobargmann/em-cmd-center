"""Status Board API -- ticket status dashboard grouped by assignee with time-in-status."""

import logging
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_

from database import get_db
from models import DeveloperRoster, TicketStatusCache, TicketStatusHistory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/status-board", tags=["status-board"])

_config: dict = {}
_refresh_runs: dict[str, dict] = {}
_refresh_lock = threading.Lock()


def set_status_board_config(config: dict) -> None:
    """Store config reference for use by endpoints."""
    global _config
    _config = config


@router.get("/dashboard")
def get_dashboard(
    project: str = Query("", description="Filter by project key"),
    priority: str = Query("", description="Filter by priority"),
    assignee: str = Query("", description="Filter by assignee display name"),
    search: str = Query("", description="Search across key, summary, assignee"),
    page: int = Query(1, ge=1),
    sort_by: str = Query("current_status_age"),
    sort_dir: str = Query("desc"),
):
    """Return the status board dashboard data."""
    db = get_db()
    try:
        # Base query: open tickets
        query = db.query(TicketStatusCache).filter(TicketStatusCache.resolved == False)

        # Apply filters
        if project:
            query = query.filter(TicketStatusCache.project_key == project)
        if priority:
            query = query.filter(TicketStatusCache.priority == priority)
        if assignee:
            query = query.filter(TicketStatusCache.assignee_display_name.ilike(f"%{assignee}%"))
        if search:
            search_term = f"%{search}%"
            query = query.filter(or_(
                TicketStatusCache.issue_key.ilike(search_term),
                TicketStatusCache.summary.ilike(search_term),
                TicketStatusCache.assignee_display_name.ilike(search_term),
            ))

        tickets = query.all()
        total_tickets = len(tickets)

        # Get roster for grouping
        roster = db.query(DeveloperRoster).filter(DeveloperRoster.active == True).all()
        roster_map = {}  # jira_account_id -> roster entry
        for r in roster:
            if r.jira_account_id:
                roster_map[r.jira_account_id] = r

        # Collect all unique statuses across tickets
        all_statuses_set = set()
        for t in tickets:
            all_statuses_set.add(t.current_status)

        # Also collect statuses from history for these tickets
        ticket_keys = [t.issue_key for t in tickets]
        history_records = []
        if ticket_keys:
            # Batch query history
            history_records = db.query(TicketStatusHistory).filter(
                TicketStatusHistory.issue_key.in_(ticket_keys)
            ).all()

        # Build per-ticket history map
        ticket_history: dict[str, list] = {}
        for h in history_records:
            ticket_history.setdefault(h.issue_key, []).append(h)
            all_statuses_set.add(h.to_status)
            if h.from_status:
                all_statuses_set.add(h.from_status)

        # Order statuses by average position in transition chains
        status_positions: dict[str, list[int]] = {}
        for key, hist_list in ticket_history.items():
            sorted_hist = sorted(hist_list, key=lambda h: h.transitioned_at)
            for i, h in enumerate(sorted_hist):
                status_positions.setdefault(h.to_status, []).append(i)
                if h.from_status and i == 0:
                    status_positions.setdefault(h.from_status, []).append(-1)

        def status_sort_key(s):
            positions = status_positions.get(s, [])
            if positions:
                return sum(positions) / len(positions)
            return 999

        statuses = sorted(all_statuses_set, key=status_sort_key)

        now = datetime.utcnow()
        jira_base = _config.get("JIRA_BASE_URL", "https://ninjio.atlassian.net")

        # Build ticket dicts with status_times
        ticket_dicts = []
        for t in tickets:
            # Aggregate time per status from history
            status_times: dict[str, int] = {}
            hist = ticket_history.get(t.issue_key, [])
            for h in hist:
                if h.from_status and h.time_in_from_seconds is not None:
                    status_times[h.from_status] = status_times.get(h.from_status, 0) + h.time_in_from_seconds

            # Current status live duration
            current_seconds = int((now - t.status_entered_at).total_seconds()) if t.status_entered_at else 0

            ticket_dicts.append({
                "issue_key": t.issue_key,
                "project_key": t.project_key,
                "summary": t.summary,
                "priority": t.priority,
                "assignee_account_id": t.assignee_account_id,
                "assignee_display_name": t.assignee_display_name,
                "current_status": t.current_status,
                "status_entered_at": t.status_entered_at.isoformat() if t.status_entered_at else None,
                "current_status_seconds": current_seconds,
                "status_times": status_times,
                "jira_url": f"{jira_base}/browse/{t.issue_key}",
            })

        # Group tickets by assignee
        roster_groups: dict[str, dict] = {}  # keyed by jira_account_id
        non_roster_tickets = []
        unassigned_tickets = []

        for td in ticket_dicts:
            aid = td["assignee_account_id"]
            if not aid:
                unassigned_tickets.append(td)
            elif aid in roster_map:
                if aid not in roster_groups:
                    r = roster_map[aid]
                    roster_groups[aid] = {
                        "assignee_name": r.display_name,
                        "assignee_type": "roster",
                        "tickets": [],
                    }
                roster_groups[aid]["tickets"].append(td)
            else:
                non_roster_tickets.append(td)

        # Sort tickets within each group
        def sort_key(td):
            if sort_by == "current_status_age":
                return td["current_status_seconds"]
            elif sort_by == "issue_key":
                return td["issue_key"]
            elif sort_by == "summary":
                return td["summary"].lower()
            elif sort_by == "priority":
                pri_order = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}
                return pri_order.get(td["priority"] or "", 5)
            elif sort_by == "current_status":
                return td["current_status"]
            elif sort_by == "project_key":
                return td["project_key"]
            elif sort_by.startswith("status_time_"):
                status_name = sort_by[len("status_time_"):]
                if status_name == td["current_status"]:
                    return td["current_status_seconds"]
                return td["status_times"].get(status_name, -1)
            return td["current_status_seconds"]

        reverse = sort_dir == "desc"

        # Build groups list
        groups = []
        for aid in sorted(roster_groups.keys(), key=lambda a: roster_groups[a]["assignee_name"].lower()):
            g = roster_groups[aid]
            g["tickets"].sort(key=sort_key, reverse=reverse)
            g["ticket_count"] = len(g["tickets"])
            groups.append(g)

        if non_roster_tickets:
            non_roster_tickets.sort(key=sort_key, reverse=reverse)
            groups.append({
                "assignee_name": "Non-Roster",
                "assignee_type": "non_roster",
                "tickets": non_roster_tickets,
                "ticket_count": len(non_roster_tickets),
            })

        if unassigned_tickets:
            unassigned_tickets.sort(key=sort_key, reverse=reverse)
            groups.append({
                "assignee_name": "Unassigned",
                "assignee_type": "unassigned",
                "tickets": unassigned_tickets,
                "ticket_count": len(unassigned_tickets),
            })

        # Pagination: flatten, paginate, then re-group
        page_size = 200
        # For simplicity, paginate at the ticket level within groups
        # but return all groups that have tickets on this page

        # Calculate summary cards from ALL open tickets (not just current page)
        # For each unique status, compute avg time for open and closed tickets
        cards = []
        for status in statuses:
            # Open avg: tickets currently in this status
            open_durations = []
            for td in ticket_dicts:
                if td["current_status"] == status:
                    open_durations.append(td["current_status_seconds"])

            # Closed avg: from history of recently closed tickets
            closed_tickets = db.query(TicketStatusCache).filter(
                TicketStatusCache.resolved == True,
            ).all()
            closed_keys = [ct.issue_key for ct in closed_tickets]
            closed_durations = []
            if closed_keys:
                closed_hist = db.query(TicketStatusHistory).filter(
                    TicketStatusHistory.issue_key.in_(closed_keys),
                    TicketStatusHistory.from_status == status,
                    TicketStatusHistory.time_in_from_seconds != None,
                ).all()
                closed_durations = [h.time_in_from_seconds for h in closed_hist if h.time_in_from_seconds is not None]

            open_avg = int(sum(open_durations) / len(open_durations)) if open_durations else None
            closed_avg = int(sum(closed_durations) / len(closed_durations)) if closed_durations else None

            cards.append({
                "status": status,
                "open_avg_seconds": open_avg,
                "closed_avg_seconds": closed_avg,
            })

        # Filter cards to only those with data
        cards = [c for c in cards if c["open_avg_seconds"] is not None or c["closed_avg_seconds"] is not None]

        # Get last synced time
        last_synced_row = db.query(func.max(TicketStatusCache.last_synced_at)).scalar()
        last_synced = last_synced_row.isoformat() if last_synced_row else None

        return {
            "cards": cards,
            "groups": groups,
            "total_tickets": total_tickets,
            "page": page,
            "page_size": page_size,
            "statuses": statuses,
            "last_synced": last_synced,
        }

    except Exception as e:
        logger.error(f"Status board dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/refresh")
def refresh_status_board():
    """Trigger a background sync of ticket statuses."""
    job_id = str(uuid.uuid4())

    with _refresh_lock:
        _refresh_runs[job_id] = {"status": "started"}

    def _run():
        try:
            from agent.status_sync import sync_ticket_statuses
            sync_ticket_statuses(_config)
            with _refresh_lock:
                _refresh_runs[job_id]["status"] = "done"
        except Exception as e:
            with _refresh_lock:
                _refresh_runs[job_id]["status"] = "error"
                _refresh_runs[job_id]["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"status": "started", "job_id": job_id}


@router.get("/ticket/{issue_key}/transitions")
def get_ticket_transitions(issue_key: str):
    """Return the status transition history for a single ticket."""
    db = get_db()
    try:
        history = db.query(TicketStatusHistory).filter(
            TicketStatusHistory.issue_key == issue_key
        ).order_by(TicketStatusHistory.transitioned_at).all()

        # Also get the ticket cache for current status info
        cache = db.query(TicketStatusCache).filter(
            TicketStatusCache.issue_key == issue_key
        ).first()

        now = datetime.utcnow()
        transitions = []

        # Build a clean timeline
        for i, h in enumerate(history):
            entered_at = h.transitioned_at
            if i + 1 < len(history):
                exited_at = history[i + 1].transitioned_at
                duration = h.time_in_from_seconds
                if duration is None and exited_at and entered_at:
                    duration = int((exited_at - entered_at).total_seconds())
            else:
                # Last transition - this is the current status
                exited_at = None
                if cache and not cache.resolved:
                    duration = int((now - entered_at).total_seconds()) if entered_at else None
                else:
                    # Resolved ticket: duration up to resolved_at or last sync
                    if cache and cache.resolved_at:
                        duration = int((cache.resolved_at - entered_at).total_seconds()) if entered_at else None
                    else:
                        duration = h.time_in_from_seconds

            transitions.append({
                "status": h.to_status,
                "entered_at": entered_at.strftime("%Y-%m-%d %H:%M") if entered_at else None,
                "exited_at": exited_at.strftime("%Y-%m-%d %H:%M") if exited_at else None,
                "duration_seconds": duration,
            })

        return {"issue_key": issue_key, "transitions": transitions}

    except Exception as e:
        logger.error(f"Transitions error for {issue_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
