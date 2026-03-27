"""Application configuration loaded from .env file."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env"


def load_config() -> dict:
    """Load and validate configuration from .env file.

    Returns:
        dict with all configuration values.

    Exits with code 1 if .env is missing or required fields are absent.
    """
    if not ENV_PATH.exists():
        print("ERROR: .env file not found.")
        print("Copy .env.example to .env and fill in your credentials:")
        print("  cp .env.example .env")
        sys.exit(1)

    load_dotenv(ENV_PATH)

    required = [
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "JIRA_TEAM_PROJECTS",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"ERROR: Missing required .env fields: {', '.join(missing)}")
        sys.exit(1)

    return {
        "JIRA_BASE_URL": os.getenv("JIRA_BASE_URL"),
        "JIRA_EMAIL": os.getenv("JIRA_EMAIL"),
        "JIRA_API_TOKEN": os.getenv("JIRA_API_TOKEN"),
        "JIRA_EM_PROJECT": os.getenv("JIRA_EM_PROJECT", "EM-TASKS"),
        "JIRA_TEAM_PROJECTS": [
            p.strip() for p in os.getenv("JIRA_TEAM_PROJECTS", "").split(",") if p.strip()
        ],
        "POLL_INTERVAL_MINUTES": int(os.getenv("POLL_INTERVAL_MINUTES", "15")),
        "GAP_DETECTION_CRON": os.getenv("GAP_DETECTION_CRON", "0 6 * * 1"),
        "STACK_RANK_CRON": os.getenv("STACK_RANK_CRON", "30 7 * * 5"),
        "REPORT_CRON": os.getenv("REPORT_CRON", "0 17 * * 4"),
        "STACK_RANK_SCRIPT": os.getenv("STACK_RANK_SCRIPT", ""),
        "CLOUD_PROVIDER": os.getenv("CLOUD_PROVIDER", ""),
        "BITBUCKET_WORKSPACE": os.getenv("BITBUCKET_WORKSPACE", ""),
        "BITBUCKET_USERNAME": os.getenv("BITBUCKET_USERNAME", ""),
        "BITBUCKET_API_TOKEN": os.getenv("BITBUCKET_API_TOKEN", ""),
        "METRICS_COLLECTION_CRON": os.getenv("METRICS_COLLECTION_CRON", "0 7 * * 1"),
        "SLACK_BOT_TOKEN": os.getenv("SLACK_BOT_TOKEN", ""),
        "SLACK_REMINDER_CRON": os.getenv("SLACK_REMINDER_CRON", "0 8 * * 1-5"),
        "SLACK_EM_USER_ID": os.getenv("SLACK_EM_USER_ID", ""),
        "APP_PORT": int(os.getenv("APP_PORT", "8765")),
        "AUTO_OPEN_BROWSER": os.getenv("AUTO_OPEN_BROWSER", "true").lower() == "true",
    }


CONFIG = load_config()
