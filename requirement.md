EM Command Center — Python Agent
Requirements for Claude Code

1. Overview
Build a local Python desktop application that serves as the Engineering Manager's daily command center. It runs on a company laptop, displays a prioritized task list pulled from Jira, and is kept current by a background automation agent that polls Jira, detects gaps, and creates or updates tasks automatically.
The user never manually enters Jira. The UI is the only interface they interact with day-to-day.

2. Architecture
┌─────────────────────────────────────────────────┐
│                Company Laptop                   │
│                                                 │
│  ┌──────────────┐    ┌────────────────────────┐ │
│  │  Python UI   │◄───│  SQLite local DB       │ │
│  │  (FastAPI +  │    │  tasks.db              │ │
│  │   HTML/JS)   │    └────────────────────────┘ │
│  └──────┬───────┘             ▲                 │
│         │ opens browser       │ writes          │
│         ▼                     │                 │
│  http://localhost:8765    ┌───┴──────────────┐  │
│                           │  Agent scheduler │  │
│                           │  (APScheduler)   │  │
│                           └───┬──────────────┘  │
│                               │                 │
└───────────────────────────────┼─────────────────┘
                                │ Jira REST API
                                ▼
                        company.atlassian.net

Stack:
Python 3.11+
FastAPI — serves the UI and REST API
APScheduler — runs background Jira polling jobs
SQLite via SQLAlchemy — local task storage
Jinja2 + vanilla HTML/JS/CSS — UI (no React, no npm)
requests or httpx — Jira API calls
python-dotenv — credentials from .env file
Single process. FastAPI app starts, spawns the scheduler in a background thread, opens the browser automatically, and serves everything on localhost:8765.

3. Configuration
All secrets and settings in a .env file at the project root. Never hardcoded.
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_API_TOKEN=your_api_token
JIRA_EM_PROJECT=EM-TASKS
JIRA_TEAM_PROJECTS=TEAM1,TEAM2,TEAM3

POLL_INTERVAL_MINUTES=15
GAP_DETECTION_CRON=0 6 * * 1
STACK_RANK_CRON=30 7 * * 5
REPORT_CRON=0 17 * * 4

APP_PORT=8765
AUTO_OPEN_BROWSER=true

On first run, if .env is missing, print clear instructions and exit with a non-zero code.

4. Data Model
4.1 Task
class Task:
    id: str                  # UUID
    title: str               # display text
    priority: str            # p1 | p2 | p3 | p4
    category: str            # delivery | people | hiring | reports | infra | email | other
    done: bool               # checked off by user
    auto: bool               # created by automation (not user)
    jira_key: str | None     # e.g. EM-DELIVERY-01
    jira_url: str | None     # full URL for click-through
    due_date: date | None    # optional due date
    created_at: datetime
    updated_at: datetime
    source: str              # "user" | "jira_gap" | "jira_recurring" | "stack_rank" | "infra"
    notes: str | None        # optional detail text

4.2 Run Log
class AgentRun:
    id: str
    job_name: str
    ran_at: datetime
    status: str              # success | error
    tasks_created: int
    tasks_updated: int
    error_message: str | None


5. UI Requirements
5.1 Layout
Single-page app served at http://localhost:8765. No page reloads — all updates via fetch calls to the FastAPI REST API.
┌─────────────────────────────────────────────┐
│  Thursday · Mar 19   ●●○○○  6/11 done  [↻] │  ← header bar
├─────────────────────────────────────────────┤
│  [Add task input................] [P2▼][Cat▼][+ Add] │
├─────────────────────────────────────────────┤
│  [All] [P1] [P2] [P3] [Open] [Done] [Auto] [Clear done] │
├─────────────────────────────────────────────┤
│  P1 — URGENT · DO NOW                       │
│  ☐  [DO NEXT] Email triage  P1 email EM-01  │
│  ☐  Sprint kickoff review   P1 delivery auto│
│                                             │
│  P2 — THIS WEEK                             │
│  ☐  Mid-sprint health check P2 delivery     │
│  ...                                        │
│                                             │
│  COMPLETED (3)                              │
│  ☑  Stack rank review ~~strikethrough~~     │
└─────────────────────────────────────────────┘

5.2 Task display
Each task row shows:
Checkbox (click to toggle done)
"DO NEXT" badge on the first open P1 task
Title (strikethrough when done)
Priority badge: P1 (red), P2 (amber), P3 (green), P4 (blue)
Category badge
"auto" badge (purple) if automation-created
Jira key as a clickable link that opens the Jira ticket in the browser
Delete button (×) visible on hover
5.3 Grouping and sort order
Tasks are grouped by priority section headers. Within each group, sort by created_at ascending (older tasks first). Done tasks always appear below all open tasks in a collapsible "Completed" section.
5.4 Add task
Input field + priority selector + category selector + Add button. Enter key submits. Clears input on save. New tasks are source="user", auto=False.
5.5 Filter bar
Filters: All, P1, P2, P3, Open, Done, Auto. One active at a time. Active filter is visually highlighted.
5.6 Refresh button
Manual [↻] button in the header triggers an immediate Jira poll (same as the scheduled job) and refreshes the UI when complete. Shows a spinner while running.
5.7 Progress indicator
X / Y done pill in the header. Updates in real time when tasks are checked.
5.8 Agent status indicator
Small status dot in the header:
Green: last agent run was successful and < 30 min ago
Amber: last run was > 30 min ago
Red: last run errored
Hovering shows last run time and status.
5.9 Dark/light mode
Respects the OS setting via CSS prefers-color-scheme. No toggle needed.

6. REST API Endpoints
All endpoints served by FastAPI at localhost:8765.
GET  /                          → serves index.html
GET  /api/tasks                 → list all tasks (supports ?filter=p1|p2|p3|open|done|auto)
POST /api/tasks                 → create task (body: title, priority, category, jira_key, notes)
PATCH /api/tasks/{id}           → update task (body: any subset of fields)
DELETE /api/tasks/{id}          → delete task
POST /api/tasks/{id}/toggle     → toggle done status
DELETE /api/tasks/done          → delete all done tasks
GET  /api/agent/status          → last run time, status, tasks_created_today
POST /api/agent/run             → trigger immediate agent run (async, returns job_id)
GET  /api/agent/run/{job_id}    → poll run status (pending | running | done | error)


7. Background Agent Jobs
Managed by APScheduler. All jobs run in background threads and write results to SQLite.
7.1 Jira gap detection (Monday 06:00, and on manual trigger)
For each project in JIRA_TEAM_PROJECTS:
Fetch all open tickets in the active sprint via Jira REST API (/rest/api/3/search with JQL).
Run gap checks on each ticket (see Section 7.5).
For each gap found, create a Task in SQLite if one with the same jira_key + gap_type does not already exist.
Mark previously-created gap tasks as done if the underlying Jira gap has been resolved.
7.2 Weekly velocity + sprint summary (Friday 07:00)
For each team project, fetch completed sprint data.
Calculate: points committed vs delivered, completion rate, tickets added mid-sprint.
Create a single "Delivery summary ready — review before sending report" Task (P2, category=reports, auto=True) with the summary in the notes field.
7.3 Stack rank trigger (Friday 07:30)
Fetch per-engineer metrics from Jira: velocity, cycle time, bugs created vs resolved, PR merge frequency (if GitHub is connected).
Run the user's existing stack ranking code by calling it as a subprocess or importing it as a module (path configured in .env as STACK_RANK_SCRIPT).
Write ranking output to ./output/stack_rank_YYYY-MM-DD.csv.
Create a Task: "Stack rank ready — review output/stack_rank_{date}.csv" (P1, category=reports, auto=True).
7.4 Infra cost check (Friday 08:00)
Pull cost data from cloud provider API (AWS Cost Explorer or GCP Billing API — configured in .env).
Compare to previous week.
Flag any service with > 20% week-over-week increase.
Create one Task per flagged service: "Infra cost spike: {service} +{pct}% vs last week" (P2, category=infra, auto=True).
7.5 Gap detection checks
For every open ticket in active sprints, create a Task if:
Gap
Task title template
Priority
Missing story points
[{jira_key}] Missing story points — {ticket_title}
P2
Missing acceptance criteria
[{jira_key}] Missing AC — {ticket_title}
P2
In Progress, no PR linked
[{jira_key}] No PR found for in-progress ticket — {ticket_title}
P2
Unassigned in active sprint
[{jira_key}] Unassigned ticket in sprint — {ticket_title}
P1
Stale (In Progress, no update > 3 days)
[{jira_key}] Stale ticket — no update in {n} days — {ticket_title}
P1
Blocked, no blocker linked
[{jira_key}] Blocked with no blocker defined — {ticket_title}
P1
Overdue (past due date, not Done)
[{jira_key}] Overdue — {ticket_title}
P1
Mid-sprint addition
[{jira_key}] Mid-sprint addition — {ticket_title}
P3
Backlog aging (no activity > 30 days)
[{jira_key}] Aging backlog ticket — {ticket_title}
P4

Deduplication rule: Before creating a gap task, check if an open task with matching jira_key and the same gap prefix already exists. If yes, skip. If the gap is resolved in Jira, mark the task done automatically.
7.6 Recurring daily tasks
Every weekday at 08:00, create the following tasks if they do not already exist for today:
DAILY_TASKS = [
    {"title": "Email triage — process Outlook inbox", "pri": "p1", "cat": "email"},
    {"title": "Standup review — check async updates, identify blockers", "pri": "p2", "cat": "people"},
    {"title": "Ticket hygiene — check for new gaps since yesterday", "pri": "p2", "cat": "delivery"},
    {"title": "End-of-day note — what moved, what is at risk", "pri": "p4", "cat": "other"},
]

On Mondays, also add:
MONDAY_EXTRAS = [
    {"title": "Sprint kickoff review — all tickets need assignee, SP, AC", "pri": "p1", "cat": "delivery"},
    {"title": "Automation brief review — read Jira gap detection report", "pri": "p1", "cat": "delivery"},
]

On Wednesdays, also add:
WEDNESDAY_EXTRAS = [
    {"title": "Mid-sprint health check — flag at-risk tickets now", "pri": "p1", "cat": "delivery"},
    {"title": "Hiring pipeline review — CV shortlist and interview schedule", "pri": "p2", "cat": "hiring"},
]

On Fridays, also add:
FRIDAY_EXTRAS = [
    {"title": "Stack rank review — check trends, note 1:1 talking points", "pri": "p1", "cat": "reports"},
    {"title": "Leadership report — finalize and send by 15:00", "pri": "p1", "cat": "reports"},
    {"title": "Infra cost check — approve optimization recommendations", "pri": "p2", "cat": "infra"},
    {"title": "Sprint retro note — one improvement item in Jira", "pri": "p3", "cat": "delivery"},
]

Deduplication rule: Check if a task with the same title already exists with created_at date = today. If yes, skip.

8. Project Structure
em-command-center/
├── main.py                  # FastAPI app entry point, starts scheduler, opens browser
├── config.py                # loads .env, validates required fields
├── models.py                # SQLAlchemy models (Task, AgentRun)
├── database.py              # DB init, session management
├── api/
│   ├── tasks.py             # task CRUD endpoints
│   └── agent.py             # agent status and trigger endpoints
├── agent/
│   ├── scheduler.py         # APScheduler setup and job registration
│   ├── gap_detection.py     # Jira gap checks → Task creation
│   ├── daily_tasks.py       # recurring daily task creation
│   ├── stack_rank.py        # stack rank trigger and file output
│   ├── infra_costs.py       # cloud cost pull and spike detection
│   └── jira_client.py       # Jira REST API wrapper
├── templates/
│   └── index.html           # single-page UI (Jinja2)
├── static/
│   ├── app.js               # all UI JS (fetch, render, interactions)
│   └── style.css            # all CSS
├── output/                  # stack rank CSVs, generated reports
├── .env                     # secrets (gitignored)
├── .env.example             # template with all keys, no values
├── requirements.txt
└── README.md


9. requirements.txt
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
sqlalchemy>=2.0.0
apscheduler>=3.10.0
httpx>=0.27.0
python-dotenv>=1.0.0
jinja2>=3.1.0
aiofiles>=23.0.0


10. README.md (Claude Code must generate this)
Must include:
Prerequisites (Python 3.11+)
Setup: git clone → pip install -r requirements.txt → copy .env.example to .env → fill in values
How to get a Jira API token (link to Atlassian docs)
How to run: python main.py
How to configure the stack rank script path
How to add cloud provider credentials for infra cost tracking
What each scheduler job does and when it runs
How to disable individual jobs (comment out in scheduler.py)

11. Error handling rules
If Jira API returns 401: log error, set agent status to red, do not crash the app.
If Jira API returns 429 (rate limit): back off 60 seconds and retry once.
If a scheduler job fails: log to AgentRun table with status=error, set status indicator to red, continue running other jobs.
If .env is missing required fields: print which fields are missing and exit before starting the server.
Never raise unhandled exceptions that crash the FastAPI server. Wrap all agent job code in try/except.

12. Security rules
API token stored only in .env, never in code or DB.
SQLite file stored in the project directory, not in a temp or shared location.
FastAPI binds only to 127.0.0.1, not 0.0.0.0.
No authentication on the local API (it is localhost-only by design).
.env and output/ are in .gitignore.

13. Claude Code instructions
When building this project:
Start with models.py and database.py. Get the DB working first.
Build jira_client.py next. Test it against the real Jira API with a simple JQL query before building anything on top of it.
Build the FastAPI app with static task CRUD before adding the scheduler.
Build and test each scheduler job independently before wiring them into APScheduler.
Build the UI last, against the working API.
Do not use React, Vue, or any JS framework. Vanilla JS only.
Do not use any CSS framework. Write all CSS from scratch matching the style of the widget described in Section 5.
The UI must work without any CDN dependencies — all assets served locally.
Use type hints on every function.
Write a docstring on every class and public function.
After completing, run python main.py and verify the server starts without errors before finishing.

