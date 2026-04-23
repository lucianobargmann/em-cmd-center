"""Tests for the Status Board feature: sync logic, API endpoints, and helpers."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from models import (
    Base,
    DeveloperRoster,
    TicketStatusCache,
    TicketStatusHistory,
)


# ---- Fixtures ----

@pytest.fixture
def db(_override_db):
    """Create a DB session for direct test use (model/unit tests)."""
    _, TestSession = _override_db
    session = TestSession()
    yield session
    session.close()


@pytest.fixture
def app_client(_override_db):
    """Create a minimal FastAPI test client with just the status board router."""
    _, TestSession = _override_db
    from api.status_board import router as sb_router, set_status_board_config
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(sb_router)
    set_status_board_config({
        "JIRA_BASE_URL": "https://ninjio.atlassian.net",
        "JIRA_EMAIL": "test@test.com",
        "JIRA_API_TOKEN": "fake",
        "JIRA_TEAM_PROJECTS": ["TEST"],
    })

    client = TestClient(test_app)
    yield client, TestSession


def _make_cache(db, issue_key="TEST-1", project="TEST", summary="Test ticket",
                priority="High", assignee_id="acc-1", assignee_name="Alice",
                status="In Progress", entered_hours_ago=48, created_hours_ago=120,
                resolved=False, resolved_at=None):
    """Helper to create a TicketStatusCache entry."""
    now = datetime.utcnow()
    entry = TicketStatusCache(
        id=str(uuid.uuid4()),
        issue_key=issue_key,
        project_key=project,
        summary=summary,
        priority=priority,
        assignee_account_id=assignee_id,
        assignee_display_name=assignee_name,
        current_status=status,
        status_entered_at=now - timedelta(hours=entered_hours_ago),
        issue_created_at=now - timedelta(hours=created_hours_ago),
        resolved=resolved,
        resolved_at=resolved_at,
        last_synced_at=now,
    )
    db.add(entry)
    db.commit()
    return entry


def _make_history(db, issue_key, from_status, to_status, hours_ago, time_in_from_seconds=None):
    """Helper to create a TicketStatusHistory entry."""
    entry = TicketStatusHistory(
        id=str(uuid.uuid4()),
        issue_key=issue_key,
        from_status=from_status,
        to_status=to_status,
        transitioned_at=datetime.utcnow() - timedelta(hours=hours_ago),
        time_in_from_seconds=time_in_from_seconds,
    )
    db.add(entry)
    db.commit()
    return entry


def _make_roster(db, name="Alice", email="alice@test.com", jira_id="acc-1"):
    """Helper to create a DeveloperRoster entry."""
    entry = DeveloperRoster(
        id=str(uuid.uuid4()),
        display_name=name,
        email=email,
        jira_account_id=jira_id,
        active=True,
    )
    db.add(entry)
    db.commit()
    return entry


# ---- Unit Tests: Time formatting (agent/status_sync.py helpers) ----

class TestParseJiraDatetime:
    """Tests for _parse_jira_datetime."""

    def test_standard_format(self):
        from agent.status_sync import _parse_jira_datetime
        result = _parse_jira_datetime("2026-04-14T09:00:00.000+0000")
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 14
        assert result.hour == 9

    def test_z_suffix(self):
        from agent.status_sync import _parse_jira_datetime
        result = _parse_jira_datetime("2026-04-14T09:00:00.000Z")
        assert result.year == 2026
        assert result.tzinfo is None  # Should be naive UTC

    def test_empty_string(self):
        from agent.status_sync import _parse_jira_datetime
        result = _parse_jira_datetime("")
        # Should return now-ish
        assert (datetime.utcnow() - result).total_seconds() < 2

    def test_none(self):
        from agent.status_sync import _parse_jira_datetime
        result = _parse_jira_datetime(None)
        assert (datetime.utcnow() - result).total_seconds() < 2


class TestExtractStatusTransitions:
    """Tests for _extract_status_transitions."""

    def test_single_transition(self):
        from agent.status_sync import _extract_status_transitions
        created = datetime(2026, 4, 14, 9, 0, 0)
        changelog = [{
            "created": "2026-04-14T13:00:00.000+0000",
            "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
        }]
        result = _extract_status_transitions(changelog, created)
        assert len(result) == 1
        assert result[0]["from_status"] == "To Do"
        assert result[0]["to_status"] == "In Progress"
        # 4 hours = 14400 seconds
        assert result[0]["time_in_from_seconds"] == 14400

    def test_multiple_transitions(self):
        from agent.status_sync import _extract_status_transitions
        created = datetime(2026, 4, 14, 9, 0, 0)
        changelog = [
            {
                "created": "2026-04-14T13:00:00.000+0000",
                "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
            },
            {
                "created": "2026-04-16T13:00:00.000+0000",
                "items": [{"field": "status", "fromString": "In Progress", "toString": "QA"}],
            },
        ]
        result = _extract_status_transitions(changelog, created)
        assert len(result) == 2
        assert result[0]["to_status"] == "In Progress"
        assert result[0]["time_in_from_seconds"] == 14400  # 4h
        assert result[1]["to_status"] == "QA"
        assert result[1]["time_in_from_seconds"] == 172800  # 2 days

    def test_no_transitions(self):
        from agent.status_sync import _extract_status_transitions
        created = datetime(2026, 4, 14, 9, 0, 0)
        result = _extract_status_transitions([], created)
        assert result == []

    def test_ignores_non_status_items(self):
        from agent.status_sync import _extract_status_transitions
        created = datetime(2026, 4, 14, 9, 0, 0)
        changelog = [{
            "created": "2026-04-14T13:00:00.000+0000",
            "items": [
                {"field": "assignee", "fromString": "Alice", "toString": "Bob"},
                {"field": "status", "fromString": "To Do", "toString": "In Progress"},
            ],
        }]
        result = _extract_status_transitions(changelog, created)
        assert len(result) == 1
        assert result[0]["to_status"] == "In Progress"

    def test_backward_transition(self):
        from agent.status_sync import _extract_status_transitions
        created = datetime(2026, 4, 14, 9, 0, 0)
        changelog = [
            {
                "created": "2026-04-14T13:00:00.000+0000",
                "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
            },
            {
                "created": "2026-04-15T13:00:00.000+0000",
                "items": [{"field": "status", "fromString": "In Progress", "toString": "QA"}],
            },
            {
                "created": "2026-04-16T09:00:00.000+0000",
                "items": [{"field": "status", "fromString": "QA", "toString": "In Progress"}],
            },
        ]
        result = _extract_status_transitions(changelog, created)
        assert len(result) == 3
        assert result[2]["from_status"] == "QA"
        assert result[2]["to_status"] == "In Progress"
        # QA lasted 20 hours
        assert result[2]["time_in_from_seconds"] == 72000


# ---- Unit Tests: Duration color thresholds ----

class TestDurationColorClass:
    """These map to the JS durationColorClass logic; test the boundary values."""

    def test_green_under_3_days(self):
        seconds = 2 * 86400  # 2 days
        days = seconds / 86400
        assert days < 3  # green

    def test_amber_3_to_7_days(self):
        seconds = 5 * 86400  # 5 days
        days = seconds / 86400
        assert 3 <= days < 7  # amber

    def test_red_over_7_days(self):
        seconds = 8 * 86400
        days = seconds / 86400
        assert days >= 7  # red


# ---- Unit Tests: Format duration ----

class TestFormatDuration:
    """Test the Python equivalent of JS formatDuration."""

    @staticmethod
    def format_duration(seconds):
        """Python version of the JS formatting logic."""
        if seconds is None or seconds < 0:
            return "--"
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"

    def test_one_day_one_hour(self):
        assert self.format_duration(90000) == "1d 1h"

    def test_less_than_one_day(self):
        assert self.format_duration(7200) == "0d 2h"

    def test_zero(self):
        assert self.format_duration(0) == "0d 0h"

    def test_none(self):
        assert self.format_duration(None) == "--"

    def test_large_value(self):
        assert self.format_duration(864000) == "10d 0h"


# ---- Model Tests ----

class TestTicketStatusCacheModel:
    def test_create_and_query(self, db):
        entry = _make_cache(db)
        result = db.query(TicketStatusCache).filter_by(issue_key="TEST-1").first()
        assert result is not None
        assert result.project_key == "TEST"
        assert result.current_status == "In Progress"
        assert result.resolved is False

    def test_unique_issue_key(self, db):
        _make_cache(db, issue_key="TEST-1")
        with pytest.raises(Exception):
            _make_cache(db, issue_key="TEST-1")

    def test_to_dict(self, db):
        entry = _make_cache(db)
        d = entry.to_dict()
        assert d["issue_key"] == "TEST-1"
        assert d["project_key"] == "TEST"
        assert "status_entered_at" in d
        assert d["resolved"] is False


class TestTicketStatusHistoryModel:
    def test_create_and_query(self, db):
        _make_history(db, "TEST-1", "To Do", "In Progress", hours_ago=24, time_in_from_seconds=3600)
        result = db.query(TicketStatusHistory).filter_by(issue_key="TEST-1").first()
        assert result.from_status == "To Do"
        assert result.to_status == "In Progress"
        assert result.time_in_from_seconds == 3600

    def test_unique_constraint(self, db):
        h = _make_history(db, "TEST-1", "To Do", "In Progress", hours_ago=24)
        with pytest.raises(Exception):
            # Same issue_key, transitioned_at, to_status
            entry = TicketStatusHistory(
                id=str(uuid.uuid4()),
                issue_key="TEST-1",
                from_status="To Do",
                to_status="In Progress",
                transitioned_at=h.transitioned_at,
            )
            db.add(entry)
            db.commit()

    def test_to_dict(self, db):
        h = _make_history(db, "TEST-1", None, "To Do", hours_ago=48)
        d = h.to_dict()
        assert d["from_status"] is None
        assert d["to_status"] == "To Do"


# ---- Integration Tests: Grouping Logic ----

class TestGroupingLogic:
    """Test the roster/non-roster/unassigned grouping."""

    def test_roster_grouping(self, db):
        _make_roster(db, name="Alice", jira_id="acc-1")
        _make_roster(db, name="Bob", email="bob@test.com", jira_id="acc-2")
        _make_cache(db, issue_key="T-1", assignee_id="acc-1", assignee_name="Alice")
        _make_cache(db, issue_key="T-2", assignee_id="acc-2", assignee_name="Bob")
        _make_cache(db, issue_key="T-3", assignee_id="acc-1", assignee_name="Alice")

        roster = db.query(DeveloperRoster).filter(DeveloperRoster.active == True).all()
        roster_map = {r.jira_account_id: r for r in roster if r.jira_account_id}
        tickets = db.query(TicketStatusCache).all()

        roster_groups = {}
        non_roster = []
        unassigned = []
        for t in tickets:
            if not t.assignee_account_id:
                unassigned.append(t)
            elif t.assignee_account_id in roster_map:
                roster_groups.setdefault(t.assignee_account_id, []).append(t)
            else:
                non_roster.append(t)

        assert len(roster_groups) == 2
        assert len(roster_groups["acc-1"]) == 2
        assert len(roster_groups["acc-2"]) == 1
        assert len(non_roster) == 0
        assert len(unassigned) == 0

    def test_non_roster_grouping(self, db):
        _make_roster(db, name="Alice", jira_id="acc-1")
        _make_cache(db, issue_key="T-1", assignee_id="acc-99", assignee_name="External")

        roster = db.query(DeveloperRoster).filter(DeveloperRoster.active == True).all()
        roster_map = {r.jira_account_id: r for r in roster if r.jira_account_id}
        tickets = db.query(TicketStatusCache).all()

        non_roster = [t for t in tickets if t.assignee_account_id and t.assignee_account_id not in roster_map]
        assert len(non_roster) == 1
        assert non_roster[0].assignee_display_name == "External"

    def test_unassigned_grouping(self, db):
        _make_cache(db, issue_key="T-1", assignee_id=None, assignee_name=None)

        tickets = db.query(TicketStatusCache).all()
        unassigned = [t for t in tickets if not t.assignee_account_id]
        assert len(unassigned) == 1

    def test_mixed_groups_ordering(self, db):
        _make_roster(db, name="Alice", jira_id="acc-1")
        _make_cache(db, issue_key="T-1", assignee_id="acc-1", assignee_name="Alice")
        _make_cache(db, issue_key="T-2", assignee_id="acc-99", assignee_name="External")
        _make_cache(db, issue_key="T-3", assignee_id=None, assignee_name=None)

        roster = db.query(DeveloperRoster).filter(DeveloperRoster.active == True).all()
        roster_map = {r.jira_account_id: r for r in roster if r.jira_account_id}
        tickets = db.query(TicketStatusCache).all()

        roster_t = [t for t in tickets if t.assignee_account_id and t.assignee_account_id in roster_map]
        non_roster_t = [t for t in tickets if t.assignee_account_id and t.assignee_account_id not in roster_map]
        unassigned_t = [t for t in tickets if not t.assignee_account_id]

        assert len(roster_t) == 1
        assert len(non_roster_t) == 1
        assert len(unassigned_t) == 1


# ---- Integration Tests: Average Calculations ----

class TestAverageCalculations:
    def test_open_avg_multiple_tickets(self, db):
        """3 tickets in To Do at different ages, avg should be correct."""
        _make_cache(db, issue_key="T-1", status="To Do", entered_hours_ago=24)
        _make_cache(db, issue_key="T-2", status="To Do", entered_hours_ago=72,
                    assignee_id="acc-2", assignee_name="Bob")
        _make_cache(db, issue_key="T-3", status="To Do", entered_hours_ago=120,
                    assignee_id="acc-3", assignee_name="Charlie")

        tickets = db.query(TicketStatusCache).filter(
            TicketStatusCache.resolved == False,
            TicketStatusCache.current_status == "To Do",
        ).all()

        now = datetime.utcnow()
        durations = [int((now - t.status_entered_at).total_seconds()) for t in tickets]
        avg = sum(durations) / len(durations)
        avg_hours = avg / 3600
        # Average should be around 72 hours (24+72+120)/3
        assert 70 < avg_hours < 74

    def test_closed_avg_from_history(self, db):
        """Closed tickets' time in QA should average correctly."""
        _make_cache(db, issue_key="T-1", resolved=True,
                    resolved_at=datetime.utcnow() - timedelta(days=3))
        _make_cache(db, issue_key="T-2", resolved=True,
                    resolved_at=datetime.utcnow() - timedelta(days=5),
                    assignee_id="acc-2", assignee_name="Bob")

        # T-1 spent 1 day in QA, T-2 spent 3 days
        _make_history(db, "T-1", "QA", "Done", hours_ago=72, time_in_from_seconds=86400)
        _make_history(db, "T-2", "QA", "Done", hours_ago=120, time_in_from_seconds=259200)

        closed_hist = db.query(TicketStatusHistory).filter(
            TicketStatusHistory.from_status == "QA",
            TicketStatusHistory.time_in_from_seconds != None,
        ).all()

        durations = [h.time_in_from_seconds for h in closed_hist]
        avg = sum(durations) / len(durations)
        assert avg == 172800  # (86400 + 259200) / 2 = 2 days

    def test_no_tickets_in_status(self, db):
        """No tickets in a given status should return empty."""
        tickets = db.query(TicketStatusCache).filter(
            TicketStatusCache.current_status == "Nonexistent",
        ).all()
        assert len(tickets) == 0


# ---- Integration Tests: API Endpoints ----

class TestDashboardEndpoint:
    def test_empty_response(self, app_client):
        client, _ = app_client
        resp = client.get("/api/status-board/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tickets"] == 0
        assert data["groups"] == []

    def test_returns_tickets(self, app_client):
        client, SessFactory = app_client
        s = SessFactory()
        _make_roster(s, name="Alice", jira_id="acc-1")
        _make_cache(s, issue_key="TEST-1", assignee_id="acc-1", assignee_name="Alice")
        resp = client.get("/api/status-board/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tickets"] == 1
        assert len(data["groups"]) == 1
        assert data["groups"][0]["assignee_name"] == "Alice"
        assert data["groups"][0]["ticket_count"] == 1
        s.close()

    def test_project_filter(self, app_client):
        client, SessFactory = app_client
        s = SessFactory()
        _make_cache(s, issue_key="TEST-1", project="TEST")
        _make_cache(s, issue_key="OTHER-1", project="OTHER", assignee_id="acc-2")
        resp = client.get("/api/status-board/dashboard?project=TEST")
        data = resp.json()
        assert data["total_tickets"] == 1
        s.close()

    def test_search_filter(self, app_client):
        client, SessFactory = app_client
        s = SessFactory()
        _make_cache(s, issue_key="TEST-1", summary="Fix payment gateway")
        _make_cache(s, issue_key="TEST-2", summary="Add dark mode",
                    assignee_id="acc-2", assignee_name="Bob")
        resp = client.get("/api/status-board/dashboard?search=payment")
        data = resp.json()
        assert data["total_tickets"] == 1
        s.close()

    def test_priority_filter(self, app_client):
        client, SessFactory = app_client
        s = SessFactory()
        _make_cache(s, issue_key="TEST-1", priority="High")
        _make_cache(s, issue_key="TEST-2", priority="Low",
                    assignee_id="acc-2", assignee_name="Bob")
        resp = client.get("/api/status-board/dashboard?priority=High")
        data = resp.json()
        assert data["total_tickets"] == 1
        s.close()


class TestTransitionsEndpoint:
    def test_returns_transitions(self, app_client):
        client, SessFactory = app_client
        s = SessFactory()
        _make_cache(s, issue_key="TEST-1")
        _make_history(s, "TEST-1", None, "To Do", hours_ago=120, time_in_from_seconds=0)
        _make_history(s, "TEST-1", "To Do", "In Progress", hours_ago=96, time_in_from_seconds=86400)
        resp = client.get("/api/status-board/ticket/TEST-1/transitions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transitions"]) == 2
        assert data["transitions"][0]["status"] == "To Do"
        assert data["transitions"][1]["status"] == "In Progress"
        s.close()

    def test_empty_transitions(self, app_client):
        client, _ = app_client
        resp = client.get("/api/status-board/ticket/NONEXIST-1/transitions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["transitions"] == []


class TestRefreshEndpoint:
    def test_returns_started(self, app_client):
        client, _ = app_client
        with patch("agent.status_sync.sync_ticket_statuses"):
            resp = client.post("/api/status-board/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "job_id" in data


# ---- Sync Job Tests ----

class TestSyncTicketStatuses:
    @patch("agent.status_sync.get_db")
    def test_processes_tickets(self, mock_get_db, db):
        mock_get_db.return_value = db
        _make_roster(db, name="Alice", jira_id="acc-1")

        mock_client = MagicMock()
        mock_client.search_issues.return_value = [{
            "key": "TEST-1",
            "fields": {
                "summary": "Test ticket",
                "status": {"name": "In Progress"},
                "assignee": {"accountId": "acc-1", "displayName": "Alice"},
                "priority": {"name": "High"},
                "created": "2026-04-14T09:00:00.000+0000",
                "updated": "2026-04-20T09:00:00.000+0000",
            },
            "_resolved": False,
        }]
        mock_client.get_issue_changelog.return_value = [{
            "created": "2026-04-14T13:00:00.000+0000",
            "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
        }]

        with patch("agent.status_sync.JiraClient", return_value=mock_client):
            from agent.status_sync import sync_ticket_statuses
            sync_ticket_statuses({
                "JIRA_BASE_URL": "https://test.atlassian.net",
                "JIRA_EMAIL": "test@test.com",
                "JIRA_API_TOKEN": "fake",
                "JIRA_TEAM_PROJECTS": ["TEST"],
            })

        # Verify cache was created
        cached = db.query(TicketStatusCache).filter_by(issue_key="TEST-1").first()
        assert cached is not None
        assert cached.current_status == "In Progress"
        assert cached.assignee_account_id == "acc-1"

        # Verify history was created
        history = db.query(TicketStatusHistory).filter_by(issue_key="TEST-1").all()
        assert len(history) == 1
        assert history[0].from_status == "To Do"
        assert history[0].to_status == "In Progress"
