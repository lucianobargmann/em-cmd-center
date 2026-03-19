# EM Command Center

A local Python desktop application that serves as the Engineering Manager's daily command center. Displays a prioritized task list pulled from Jira, kept current by a background automation agent.

## Prerequisites

- Python 3.11+
- A Jira Cloud instance with API access

## Setup

```bash
git clone <repo-url> && cd em-command-center
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
```

## Getting a Jira API Token

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **Create API token**
3. Give it a label (e.g. "EM Command Center")
4. Copy the token into `JIRA_API_TOKEN` in your `.env` file

## Running

```bash
python main.py
```

Opens automatically at http://localhost:8765.

## Configuration

All settings in `.env`:

| Variable | Description |
|---|---|
| `JIRA_BASE_URL` | Your Jira instance URL (e.g. `https://ninjio.atlassian.net`) |
| `JIRA_EMAIL` | Your Jira account email |
| `JIRA_API_TOKEN` | API token from Atlassian |
| `JIRA_EM_PROJECT` | Project key for EM tasks |
| `JIRA_TEAM_PROJECTS` | Comma-separated project keys to monitor (e.g. `SAOP,SAOP2`) |
| `POLL_INTERVAL_MINUTES` | How often to poll Jira (default: 15) |
| `STACK_RANK_SCRIPT` | Path to your stack ranking Python script (optional) |
| `CLOUD_PROVIDER` | Set to `aws` to enable infra cost tracking (optional) |
| `APP_PORT` | Server port (default: 8765) |
| `AUTO_OPEN_BROWSER` | Auto-open browser on start (default: true) |

## Stack Rank Script

Set `STACK_RANK_SCRIPT` in `.env` to the path of your ranking script. It will be called with:

```bash
python <script> <input_csv> <output_csv>
```

The input CSV contains columns: `name`, `tickets_completed`, `story_points`.

## Cloud Cost Tracking

Set `CLOUD_PROVIDER=aws` and configure AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) to enable weekly infra cost spike detection.

## Scheduler Jobs

| Job | Schedule | What it does |
|---|---|---|
| Jira poll + daily tasks | Every 15 min | Syncs Jira gaps, creates daily recurring tasks |
| Gap detection | Monday 06:00 | Deep scan for missing SP, AC, unassigned tickets, etc. |
| Daily recurring tasks | Weekdays 08:00 | Creates day-of-week specific tasks |
| Velocity summary | Friday 07:00 | Sprint completion metrics per project |
| Stack rank | Friday 07:30 | Runs ranking script, outputs CSV |
| Infra cost check | Friday 08:00 | Detects >20% week-over-week cost increases |

## Disabling Jobs

Comment out the relevant `scheduler.add_job(...)` block in `agent/scheduler.py`.
