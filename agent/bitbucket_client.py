"""Bitbucket Cloud REST API wrapper for fetching repos, commits, and PRs."""

import logging
import time
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bitbucket.org/2.0"


class BitbucketClient:
    """Wrapper around the Bitbucket Cloud REST API v2.0.

    Handles authentication, pagination, and rate-limit retries.
    Supports both App Passwords (Basic auth) and Workspace Access Tokens (Bearer).
    """

    def __init__(self, workspace: str, api_token: str, username: str = "") -> None:
        self.workspace = workspace
        self.client = httpx.Client(timeout=30.0)
        if username:
            # App Password → Basic auth (username:app_password)
            import base64
            cred = base64.b64encode(f"{username}:{api_token}".encode()).decode()
            self.headers = {"Authorization": f"Basic {cred}"}
        else:
            # Workspace Access Token → Bearer auth
            self.headers = {"Authorization": f"Bearer {api_token}"}
        self.api_calls = 0

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make an authenticated request with rate-limit retry."""
        self.api_calls += 1
        resp = self.client.request(method, url, headers=self.headers, **kwargs)

        if resp.status_code == 429:
            logger.warning("Bitbucket rate limit hit, backing off 60s")
            time.sleep(60)
            resp = self.client.request(method, url, headers=self.headers, **kwargs)

        resp.raise_for_status()
        return resp

    def _paginate(self, url: str, params: dict | None = None) -> list[dict]:
        """Follow Bitbucket pagination (next URL pattern)."""
        all_values: list[dict] = []
        next_url = url

        while next_url:
            resp = self._request("GET", next_url, params=params)
            data = resp.json()
            all_values.extend(data.get("values", []))
            next_url = data.get("next")
            # Clear params after first request — next URL includes them
            params = None

        return all_values

    def get_repos(self) -> list[dict]:
        """List all repos in the workspace."""
        url = f"{BASE_URL}/repositories/{self.workspace}"
        return self._paginate(url, params={"pagelen": 100})

    def get_commits_in_range(
        self, repo_slug: str, since: datetime, until: datetime, branch: str = "main"
    ) -> list[dict]:
        """Get commits in a date range for a repo.

        Bitbucket commits API doesn't support date filtering, so we paginate
        newest-first and stop when we go past the since date.
        """
        url = f"{BASE_URL}/repositories/{self.workspace}/{repo_slug}/commits"
        all_commits: list[dict] = []
        next_url = url
        params: dict | None = {"pagelen": 50, "include": branch}

        while next_url:
            try:
                resp = self._request("GET", next_url, params=params)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    # Branch doesn't exist in this repo
                    return all_commits
                raise
            data = resp.json()
            params = None

            for commit in data.get("values", []):
                commit_date_str = commit.get("date", "")
                try:
                    commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue

                if commit_date.replace(tzinfo=None) < since:
                    return all_commits
                if commit_date.replace(tzinfo=None) <= until:
                    all_commits.append(commit)

            next_url = data.get("next")

        return all_commits

    def get_diffstat(self, repo_slug: str, commit_hash: str) -> int:
        """Get total lines changed (added + removed) for a commit."""
        url = f"{BASE_URL}/repositories/{self.workspace}/{repo_slug}/diffstat/{commit_hash}"
        try:
            values = self._paginate(url)
            total = 0
            for entry in values:
                total += entry.get("lines_added", 0) + entry.get("lines_removed", 0)
            return total
        except httpx.HTTPStatusError:
            logger.warning(f"Failed to get diffstat for {repo_slug}/{commit_hash}")
            return 0

    def get_merged_prs_in_range(self, repo_slug: str, since: datetime) -> list[dict]:
        """Get merged PRs updated after since date."""
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
        url = f"{BASE_URL}/repositories/{self.workspace}/{repo_slug}/pullrequests"
        params = {
            "state": "MERGED",
            "q": f'updated_on>"{since_str}"',
            "pagelen": 50,
        }
        try:
            return self._paginate(url, params=params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []
            raise
