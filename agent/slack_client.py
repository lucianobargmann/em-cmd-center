"""Slack Web API wrapper for sending DMs and listing users."""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://slack.com/api"


class SlackClient:
    """Wrapper around the Slack Web API.

    Handles Bearer token auth, pagination, and rate-limit retries.
    """

    def __init__(self, bot_token: str) -> None:
        self.client = httpx.Client(timeout=30.0)
        self.headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """Make an authenticated request with rate-limit retry.

        Returns:
            Parsed JSON response dict.

        Raises:
            RuntimeError: If Slack returns ok=false.
        """
        resp = self.client.request(method, url, headers=self.headers, **kwargs)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "30"))
            logger.warning("Slack rate limit hit, backing off %ds", retry_after)
            time.sleep(retry_after)
            resp = self.client.request(method, url, headers=self.headers, **kwargs)

        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            raise RuntimeError(f"Slack API error: {error}")

        return data

    def list_users(self) -> list[dict]:
        """Fetch all active non-bot users from the workspace (paginated).

        Returns:
            List of dicts with id, real_name, email, display_name.
        """
        all_users: list[dict] = []
        cursor = None

        while True:
            params: dict = {"limit": 200}
            if cursor:
                params["cursor"] = cursor

            data = self._request("GET", f"{BASE_URL}/users.list", params=params)

            for member in data.get("members", []):
                if member.get("deleted") or member.get("is_bot") or member.get("id") == "USLACKBOT":
                    continue
                profile = member.get("profile", {})
                all_users.append({
                    "id": member["id"],
                    "real_name": member.get("real_name", profile.get("real_name", "")),
                    "display_name": profile.get("display_name", ""),
                    "email": profile.get("email", ""),
                })

            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        return all_users

    def open_dm(self, user_id: str) -> str:
        """Open a DM channel with a user.

        Returns:
            Channel ID for the DM conversation.
        """
        data = self._request("POST", f"{BASE_URL}/conversations.open", json={"users": user_id})
        return data["channel"]["id"]

    def send_message(self, channel: str, text: str, blocks: list | None = None) -> dict:
        """Send a message to a channel or DM.

        Returns:
            Slack message response dict.
        """
        payload: dict = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        return self._request("POST", f"{BASE_URL}/chat.postMessage", json=payload)

    def send_dm(self, user_id: str, text: str, blocks: list | None = None) -> dict:
        """Open a DM channel and send a message to a user.

        Returns:
            Slack message response dict.
        """
        channel = self.open_dm(user_id)
        return self.send_message(channel, text, blocks)
