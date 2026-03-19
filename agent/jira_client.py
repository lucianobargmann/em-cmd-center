"""Jira REST API wrapper for fetching sprint data, tickets, and changelogs."""

import logging
import time
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


class JiraClient:
    """Wrapper around the Jira REST API v3.

    Handles authentication, pagination, and rate-limit retries.
    """

    def __init__(self, base_url: str, email: str, api_token: str) -> None:
        """Initialize with Jira credentials.

        Args:
            base_url: e.g. https://ninjio.atlassian.net
            email: Jira account email.
            api_token: Jira API token.
        """
        self.base_url = base_url.rstrip("/")
        self.auth = (email, api_token)
        self.client = httpx.Client(timeout=30.0)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request with rate-limit retry.

        Args:
            method: HTTP method.
            path: API path (appended to base_url).
            **kwargs: Passed to httpx.

        Returns:
            httpx.Response object.

        Raises:
            httpx.HTTPStatusError: On non-retryable errors.
        """
        url = f"{self.base_url}{path}"
        resp = self.client.request(method, url, auth=self.auth, **kwargs)

        if resp.status_code == 429:
            logger.warning("Jira rate limit hit, backing off 60s")
            time.sleep(60)
            resp = self.client.request(method, url, auth=self.auth, **kwargs)

        if resp.status_code == 401:
            logger.error("Jira API returned 401 Unauthorized")
            raise httpx.HTTPStatusError("Jira 401 Unauthorized", request=resp.request, response=resp)

        resp.raise_for_status()
        return resp

    def search_issues(self, jql: str, fields: list[str] | None = None, max_results: int = 100) -> list[dict]:
        """Search Jira issues using JQL with pagination.

        Args:
            jql: JQL query string.
            fields: List of field names to return.
            max_results: Max results per page.

        Returns:
            List of issue dicts.
        """
        all_issues: list[dict] = []
        next_page_token = None

        while True:
            payload: dict = {
                "jql": jql,
                "maxResults": max_results,
            }
            if fields:
                payload["fields"] = fields
            if next_page_token:
                payload["nextPageToken"] = next_page_token

            resp = self._request(
                "POST", "/rest/api/3/search/jql",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            data = resp.json()
            issues = data.get("issues", [])
            all_issues.extend(issues)

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        return all_issues

    def get_active_sprint_issues(self, project_key: str) -> list[dict]:
        """Fetch all open issues in the active sprint for a project.

        Args:
            project_key: Jira project key (e.g. SAOP).

        Returns:
            List of issue dicts with relevant fields.
        """
        jql = (
            f"project = {project_key} AND sprint in openSprints() "
            f"AND status != Done ORDER BY priority ASC"
        )
        fields = [
            "summary", "status", "assignee", "priority",
            "customfield_10016",  # story points
            "description", "duedate", "created", "updated",
            "issuelinks", "comment",
        ]
        return self.search_issues(jql, fields)

    def get_completed_sprint_data(self, project_key: str) -> list[dict]:
        """Fetch issues from the most recently completed sprint.

        Args:
            project_key: Jira project key.

        Returns:
            List of issue dicts.
        """
        jql = (
            f"project = {project_key} AND sprint in closedSprints() "
            f"AND resolved >= -14d ORDER BY resolved DESC"
        )
        fields = [
            "summary", "status", "assignee", "customfield_10016",
            "resolutiondate", "created",
        ]
        return self.search_issues(jql, fields)

    def get_issue_changelog(self, issue_key: str) -> list[dict]:
        """Fetch changelog for a specific issue.

        Args:
            issue_key: e.g. SAOP-123.

        Returns:
            List of changelog entry dicts.
        """
        try:
            resp = self._request("GET", f"/rest/api/3/issue/{issue_key}/changelog")
            return resp.json().get("values", [])
        except Exception:
            logger.warning(f"Failed to fetch changelog for {issue_key}")
            return []

    def get_issue_has_pr(self, issue_key: str) -> bool:
        """Check if an issue has linked pull requests via dev info.

        Falls back to checking issue links for PR-like links.

        Args:
            issue_key: e.g. SAOP-123.

        Returns:
            True if a PR link is found.
        """
        try:
            resp = self._request("GET", f"/rest/api/3/issue/{issue_key}", params={"fields": "issuelinks"})
            data = resp.json()
            links = data.get("fields", {}).get("issuelinks", [])
            for link in links:
                link_type = link.get("type", {}).get("name", "").lower()
                if "pull" in link_type or "pr" in link_type or "review" in link_type:
                    return True
            # Also check remote links
            try:
                resp2 = self._request("GET", f"/rest/api/3/issue/{issue_key}/remotelink")
                remote_links = resp2.json()
                for rl in remote_links:
                    url = rl.get("object", {}).get("url", "")
                    if "github.com" in url and "/pull/" in url:
                        return True
                    if "bitbucket" in url and "/pull-requests/" in url:
                        return True
            except Exception:
                pass
            return False
        except Exception:
            return False

    def create_issue(
        self, project_key: str, summary: str, description: str = "", issue_type: str = "Task"
    ) -> dict:
        """Create a new Jira issue.

        Args:
            project_key: Jira project key (e.g. EM-TASKS).
            summary: Issue summary/title.
            description: Issue description (plain text, converted to ADF).
            issue_type: Issue type name (default Task).

        Returns:
            Dict with 'key' and 'url' of the created issue.
        """
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type},
            }
        }
        if description:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }

        resp = self._request(
            "POST",
            "/rest/api/3/issue",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        key = data["key"]
        url = f"{self.base_url}/browse/{key}"
        return {"key": key, "url": url}

    def update_issue(
        self, issue_key: str, fields: dict | None = None, transition_name: str | None = None
    ) -> None:
        """Update a Jira issue's fields and/or transition its status.

        Args:
            issue_key: e.g. EM-TASKS-42.
            fields: Dict of fields to update (e.g. {"summary": "new title"}).
            transition_name: Name of transition to apply (e.g. "Done").
        """
        if fields:
            update_payload: dict = {"fields": {}}
            for k, v in fields.items():
                if k == "description" and isinstance(v, str):
                    update_payload["fields"]["description"] = {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": v}],
                            }
                        ],
                    }
                else:
                    update_payload["fields"][k] = v
            self._request(
                "PUT",
                f"/rest/api/3/issue/{issue_key}",
                json=update_payload,
                headers={"Content-Type": "application/json"},
            )

        if transition_name:
            # Get available transitions
            resp = self._request("GET", f"/rest/api/3/issue/{issue_key}/transitions")
            transitions = resp.json().get("transitions", [])
            for t in transitions:
                if t["name"].lower() == transition_name.lower():
                    self._request(
                        "POST",
                        f"/rest/api/3/issue/{issue_key}/transitions",
                        json={"transition": {"id": t["id"]}},
                        headers={"Content-Type": "application/json"},
                    )
                    break

    def is_stale(self, issue: dict, days: int = 3) -> tuple[bool, int]:
        """Check if an in-progress issue has had no updates recently.

        Args:
            issue: Jira issue dict.
            days: Number of days to consider stale.

        Returns:
            Tuple of (is_stale, days_since_update).
        """
        updated = issue.get("fields", {}).get("updated", "")
        if not updated:
            return False, 0
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        now = datetime.now(updated_dt.tzinfo)
        delta = now - updated_dt
        return delta > timedelta(days=days), delta.days
