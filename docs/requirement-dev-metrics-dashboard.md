# Requirement: Developer Metrics Dashboard

> **Status:** Approved
> **Date:** 2026-03-24
> **Author:** EM Command Center
> **Mockup:** `/tmp/mockup-dev-metrics.html`

---

## Context

Developer performance metrics are currently scattered across two disconnected systems:

1. **eps-metrics** (Google Sheets + Apps Script) — calculates EPS scores using a 7-factor formula, requires manual CSV imports for AI usage and manual entry for incidents/code reviews.
2. **~/code/metrics** (Python scripts) — calculates DORA metrics (cycle time, lead time) from Jira, exports to JSON/Excel, posts weekly Slack summaries via n8n.

This fragmentation forces the EM to manually run scripts across both systems, paste CSV data, and cross-reference reports. The two official metrics required for leadership reporting — **average weekly cycle time** and **average weekly lead time** — require running separate Python commands.

## Goal

Build an automated, unified **Developer Metrics** section inside the EM Command Center that:

- Runs every Monday morning (07:00 cron) with zero manual intervention
- Collects data from Jira and Bitbucket APIs automatically
- Presents a visual dashboard with 7 metric panels + detail table
- Generates a copyable text report for leadership
- Retains 12 weeks of history for trend analysis
- Replaces the need to run `eps-metrics` and `~/code/metrics` separately for weekly reporting

---

## Scope

### In Scope

- New "Metrics" tab in the Command Center SPA (alongside existing Tasks view)
- Developer roster management (CRUD) for cross-system identity mapping
- Automated Monday morning data collection from Jira + Bitbucket
- 7 dashboard panels:
  1. Lines committed per developer (from Bitbucket)
  2. PR count per developer (from Bitbucket)
  3. Tickets by status bucket per developer (from Jira)
  4. Story points by status bucket per developer (from Jira)
  5. Story points / cycle time ratio per developer
  6. Defects: total, new, closed, trend — stacked column by P1/P2/other (from Jira)
  7. Simplified EPS score (PS × QM × VM) per developer
- Detailed cycle time & lead time table per developer with WoW trends
- Official metrics (team avg cycle time + lead time) prominently displayed
- Week navigator (12 weeks of history)
- Copyable text report (matching existing Daily Report UX pattern)
- Manual "re-collect" trigger button
- Empty, loading, and error states

### Out of Scope

- Slack integration (deferred — MVP skips automated Slack posting)
- Manual EPS components (AI multiplier, code review bonus, leadership multiplier, incident penalty)
- Real-time / live-updating dashboard (data is snapshot-based, collected weekly)
- Per-repository breakdown (metrics are aggregated across all repos)
- Mobile-specific responsive layout
- Authentication / multi-user access (EM-only local tool)

---

## Technical Specification

### Data Model

Three new tables in `tasks.db`:

#### `developer_roster`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | String(36) | PK, UUID | Unique identifier |
| display_name | String(200) | NOT NULL | Human-readable name (e.g., "M. Batistela") |
| email | String(200) | NOT NULL, UNIQUE | Primary identity key |
| jira_account_id | String(100) | NULLABLE | Jira Cloud account ID for assignee matching |
| bitbucket_username | String(100) | NULLABLE | Bitbucket display name / UUID for commit matching |
| team | String(50) | DEFAULT 'engineering' | Team grouping |
| role | String(50) | DEFAULT 'Engineer' | Role (Engineer, Senior Engineer, Tech Lead, etc.) |
| start_date | Date | NULLABLE | Hire date (for tenure-based normalization) |
| active | Boolean | DEFAULT true | Whether to include in collection |
| created_at | DateTime | DEFAULT utcnow | |
| updated_at | DateTime | DEFAULT utcnow | |

#### `weekly_snapshots`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | String(36) | PK, UUID | |
| week_start | Date | NOT NULL | Monday of the measured week |
| developer_id | String(36) | FK → developer_roster.id, NOT NULL | |
| lines_committed | Integer | DEFAULT 0 | Git lines added+removed |
| pr_count | Integer | DEFAULT 0 | PRs merged/created in period |
| tickets_todo | Integer | DEFAULT 0 | Tickets in TODO statuses |
| tickets_wip | Integer | DEFAULT 0 | Tickets in WIP statuses |
| tickets_qa | Integer | DEFAULT 0 | Tickets in QA/UAT statuses |
| tickets_closed | Integer | DEFAULT 0 | Tickets in Done statuses |
| sp_todo | Integer | DEFAULT 0 | Story points in TODO |
| sp_wip | Integer | DEFAULT 0 | Story points in WIP |
| sp_qa | Integer | DEFAULT 0 | Story points in QA/UAT |
| sp_closed | Integer | DEFAULT 0 | Story points in Done |
| cycle_time_mean | Float | NULLABLE | Mean cycle time in days |
| cycle_time_median | Float | NULLABLE | Median cycle time |
| cycle_time_p85 | Float | NULLABLE | 85th percentile cycle time |
| lead_time_mean | Float | NULLABLE | Mean lead time in days |
| lead_time_median | Float | NULLABLE | Median lead time |
| lead_time_p85 | Float | NULLABLE | 85th percentile lead time |
| defects_total | Integer | DEFAULT 0 | Total open defects (snapshot) |
| defects_new | Integer | DEFAULT 0 | Bugs opened this week |
| defects_closed | Integer | DEFAULT 0 | Bugs closed this week |
| defects_p1 | Integer | DEFAULT 0 | Open defects with label 'p1' |
| defects_p2 | Integer | DEFAULT 0 | Open defects with label 'p2' |
| defects_other | Integer | DEFAULT 0 | Open defects without p1/p2 label |
| eps_productivity | Float | NULLABLE | PS component |
| eps_quality | Float | NULLABLE | QM component |
| eps_velocity | Float | NULLABLE | VM component |
| eps_score | Float | NULLABLE | PS × QM × VM |
| created_at | DateTime | DEFAULT utcnow | |

**Unique constraint:** `(week_start, developer_id)` — enables upsert on re-collection.

#### `weekly_team_summary`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | String(36) | PK, UUID | |
| week_start | Date | NOT NULL, UNIQUE | Monday of the measured week |
| total_lines | Integer | DEFAULT 0 | Team total lines committed |
| total_prs | Integer | DEFAULT 0 | Team total PRs |
| total_tickets_closed | Integer | DEFAULT 0 | Team total tickets closed |
| total_sp_closed | Integer | DEFAULT 0 | Team total SP closed |
| avg_cycle_time | Float | NULLABLE | Team average cycle time (official metric) |
| avg_lead_time | Float | NULLABLE | Team average lead time (official metric) |
| avg_cycle_time_median | Float | NULLABLE | Team median cycle time |
| avg_lead_time_median | Float | NULLABLE | Team median lead time |
| defects_total | Integer | DEFAULT 0 | Team total open defects |
| defects_new | Integer | DEFAULT 0 | Team bugs opened this week |
| defects_closed | Integer | DEFAULT 0 | Team bugs closed this week |
| defects_p1 | Integer | DEFAULT 0 | |
| defects_p2 | Integer | DEFAULT 0 | |
| defects_other | Integer | DEFAULT 0 | |
| created_at | DateTime | DEFAULT utcnow | |

**Retention:** 12 weeks rolling. On each collection, delete snapshots where `week_start < (current Monday - 12 weeks)`.

### Status Bucket Mapping

| Dashboard Bucket | Jira Statuses |
|-----------------|---------------|
| TODO | To Do, Open, Backlog |
| WIP | Dev In Progress, In Review, Reopened, Blocked |
| QA/UAT | QA Pending, QA In Progress, UAT |
| Closed | Done, Resolved, Closed, Released, Deployed |

### Defect Classification

- **Is a defect:** Jira issue type = `Bug`
- **P1:** Bug with label `p1`
- **P2:** Bug with label `p2`
- **Other:** Bug without `p1` or `p2` label
- **New (this week):** Bug created within Mon–Sun window
- **Closed (this week):** Bug resolved within Mon–Sun window
- **Total:** All open (unresolved) bugs snapshot at collection time

### Defect Trend Calculation

- **Week-over-week:** Compare `defects_new` current week vs previous week. Up (more new) = bad, Down (fewer new) = good.
- **4-week rolling average:** Mean of `defects_new` over last 4 weeks. Shown as context.
- **Direction indicator:** `▲` (red, bad) if WoW new increased, `▼` (green, good) if decreased, `—` if same.

### EPS Simplified Formula

```
EPS = PS × QM × VM
```

| Component | Calculation | Source |
|-----------|-------------|--------|
| **PS** (Productivity Score) | Sum of (Story Points × Complexity Weight) for closed tickets | Jira |
| **QM** (Quality Multiplier) | 1 - (bounce_rate × severity_factor), floored at 0.3 | Jira changelog (QA bounce detection) |
| **VM** (Velocity Multiplier) | Tiered from SP/day of cycle time | Jira changelog |

**Complexity Weights:** Trivial (≤2 SP) = 0.5x, Standard (3-7 SP) = 1.0x, Complex (≥8 SP) = 1.5x

**Velocity Tiers:**
| SP/day | Multiplier |
|--------|-----------|
| ≥ 3.0 | 1.3 |
| 2.0–3.0 | 1.2 |
| 1.0–2.0 | 1.1 |
| 0.5–1.0 | 1.0 |
| 0.25–0.5 | 0.9 |
| < 0.25 | 0.8 |

**EPS Status Labels:**
| Score Range | Label |
|------------|-------|
| ≥ 35 | Leading |
| 25–34 | Steady |
| 15–24 | Ramping |
| < 15 | Emerging |

### API Changes

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/metrics/dashboard?week_start=YYYY-MM-DD` | Full dashboard data for a week | None (local) |
| GET | `/api/metrics/report?week_start=YYYY-MM-DD` | Copyable text report | None (local) |
| GET | `/api/metrics/developers` | List all roster developers | None (local) |
| POST | `/api/metrics/developers` | Add developer to roster | None (local) |
| PATCH | `/api/metrics/developers/{id}` | Update developer | None (local) |
| DELETE | `/api/metrics/developers/{id}` | Remove developer (soft: set active=false) | None (local) |
| POST | `/api/metrics/collect` | Manually trigger weekly collection | None (local) |
| GET | `/api/metrics/collect/{job_id}` | Poll collection job status | None (local) |

#### GET `/api/metrics/dashboard` Response Shape

```json
{
  "week_start": "2026-03-17",
  "official_metrics": {
    "avg_cycle_time": 4.2,
    "avg_lead_time": 8.7,
    "prev_cycle_time": 4.8,
    "prev_lead_time": 9.1
  },
  "developers": [
    {
      "id": "uuid",
      "name": "M. Batistela",
      "lines_committed": 3412,
      "pr_count": 8,
      "tickets": { "todo": 2, "wip": 5, "qa": 3, "closed": 10 },
      "story_points": { "todo": 5, "wip": 13, "qa": 8, "closed": 39 },
      "cycle_time": { "mean": 2.1, "median": 1.8, "p85": 3.8 },
      "lead_time": { "mean": 5.4, "median": 4.2, "p85": 9.2 },
      "sp_per_day": 2.8,
      "eps": { "score": 38.2, "ps": 48.5, "qm": 0.95, "vm": 1.2, "label": "Leading" },
      "wow_cycle_time_delta": -1.2
    }
  ],
  "defects": {
    "total": 23,
    "new": 5,
    "closed": 7,
    "p1": 2,
    "p2": 5,
    "other": 16,
    "trend": "down",
    "wow_delta": -2,
    "four_week_avg": 6.5
  },
  "defect_history": [
    { "week_start": "2026-02-10", "p1": 2, "p2": 4, "other": 6 },
    { "week_start": "2026-02-17", "p1": 3, "p2": 5, "other": 4 }
  ],
  "weeks_available": ["2026-03-17", "2026-03-10", "2026-03-03"]
}
```

### External API Integrations

#### Bitbucket Cloud API

- **Base URL:** `https://api.bitbucket.org/2.0`
- **Auth:** Bearer token (HTTP header `Authorization: Bearer <BITBUCKET_API_TOKEN>`)
- **Workspace:** `dcoya`

| API Call | Purpose | Endpoint |
|----------|---------|----------|
| List repos | Get all repos in workspace | `GET /repositories/dcoya?pagelen=100` (paginate) |
| List commits | Lines per author per repo | `GET /repositories/dcoya/{repo}/commits?include=main&since=YYYY-MM-DD` |
| Commit diffstat | Lines added/removed | `GET /repositories/dcoya/{repo}/diffstat/{commit_hash}` |
| List PRs | PR count per author | `GET /repositories/dcoya/{repo}/pullrequests?state=MERGED&q=updated_on>"YYYY-MM-DD"` |

**Rate limiting:** Bitbucket Cloud allows 1,000 requests/hour. For large repo counts, batch and respect pagination.

**Author matching:** Match commit author email → `developer_roster.email`. Match PR author display_name or UUID → `developer_roster.bitbucket_username`.

#### Jira Cloud API (existing)

Uses existing `agent/jira_client.py` with extensions:

| Data Needed | JQL Query |
|-------------|-----------|
| Tickets by status (all devs) | `project in (SAOP, SAOP2) AND assignee in (...)` |
| Closed this week | `project in (SAOP, SAOP2) AND resolved >= "YYYY-MM-DD" AND resolved <= "YYYY-MM-DD"` |
| Defects (all open) | `project in (SAOP, SAOP2) AND type = Bug AND resolution = Unresolved` |
| Defects (new this week) | `project in (SAOP, SAOP2) AND type = Bug AND created >= "YYYY-MM-DD"` |
| Defects (closed this week) | `project in (SAOP, SAOP2) AND type = Bug AND resolved >= "YYYY-MM-DD"` |
| Changelog (for cycle/lead time) | Existing `get_issue_changelog()` method |

### Configuration Additions (`.env`)

```
# Bitbucket Cloud
BITBUCKET_WORKSPACE=dcoya
BITBUCKET_API_TOKEN=<token>

# Metrics collection schedule
METRICS_COLLECTION_CRON=0 7 * * 1
```

### Scheduler Addition

New job registered in `agent/scheduler.py`:

| Job ID | Trigger | Function | Description |
|--------|---------|----------|-------------|
| `weekly_metrics` | CronTrigger(day_of_week='mon', hour=7) | `collect_weekly_metrics()` | Full Bitbucket + Jira collection |

---

## Affected Areas

### New Files

| File | Purpose |
|------|---------|
| `agent/bitbucket_client.py` | Bitbucket Cloud REST API wrapper (repos, commits, PRs, diffstat) |
| `agent/metrics_collector.py` | Orchestrates weekly collection: calls Bitbucket + Jira, computes EPS, writes snapshots |
| `api/metrics.py` | REST endpoints for dashboard, report, roster CRUD, manual trigger |

### Modified Files

| File | Change |
|------|--------|
| `models.py` | Add `DeveloperRoster`, `WeeklySnapshot`, `WeeklyTeamSummary` models |
| `config.py` | Add `BITBUCKET_WORKSPACE`, `BITBUCKET_API_TOKEN`, `METRICS_COLLECTION_CRON` |
| `main.py` | Register `metrics_router`, pass config to metrics module |
| `agent/scheduler.py` | Register `weekly_metrics` cron job |
| `agent/jira_client.py` | Add methods for defect queries and batch assignee-based queries |
| `static/app.js` | Add Metrics tab, dashboard rendering, week navigation, report view |
| `static/style.css` | Add dashboard card styles, bar charts, stacked bars, table styles |
| `templates/index.html` | Add nav tabs, metrics container div |

### Database

- Three new tables auto-created via `Base.metadata.create_all()` (no migration needed, consistent with existing pattern)

---

## Test Plan

### Unit Tests

**File:** `tests/test_metrics_collector.py`

| Test Case | Input | Expected |
|-----------|-------|----------|
| Status bucket mapping | Jira status "Dev In Progress" | Mapped to "wip" |
| Status bucket mapping | Jira status "QA Pending" | Mapped to "qa" |
| Status bucket mapping | Jira status "Done" | Mapped to "closed" |
| Defect classification | Bug with label ['p1'] | defects_p1 incremented |
| Defect classification | Bug with labels ['feature', 'p2'] | defects_p2 incremented |
| Defect classification | Bug with labels ['ui'] | defects_other incremented |
| EPS calculation | PS=40, QM=0.95, VM=1.2 | EPS = 45.6 |
| EPS label | Score 38.2 | "Leading" |
| EPS label | Score 27.1 | "Steady" |
| EPS label | Score 18.4 | "Ramping" |
| EPS label | Score 11.3 | "Emerging" |
| Complexity weight | 2 SP ticket | Weight = 0.5 |
| Complexity weight | 5 SP ticket | Weight = 1.0 |
| Complexity weight | 13 SP ticket | Weight = 1.5 |
| Velocity tier | 2.5 SP/day | VM = 1.2 |
| Velocity tier | 0.7 SP/day | VM = 1.0 |
| Cycle time zero guard | Cycle time = 0 | Use 0.1 day minimum |
| QM floor | bounce_rate = 1.0, severity = 1.0 | QM = 0.3 (floored) |
| Defect trend WoW | This week 5 new, last week 7 new | trend = "down" (good) |
| Defect trend WoW | This week 8 new, last week 5 new | trend = "up" (bad) |
| 4-week rolling avg | weeks [5, 7, 6, 8] | avg = 6.5 |
| 4-week rolling avg | Only 1 week of data | avg = that week's value |
| Week boundary | Monday 00:00 UTC start | Includes Monday, excludes next Monday |
| Snapshot upsert | Same week_start + developer_id | Overwrites, doesn't duplicate |
| 12-week rollover | 13 weeks in DB | Oldest deleted after collection |
| Missing SP field | Ticket has no story points | Counted as 0 SP |
| Developer not in BB | Roster member, no commits found | lines=0, prs=0 |

**File:** `tests/test_bitbucket_client.py`

| Test Case | Input | Expected |
|-----------|-------|----------|
| List repos pagination | >100 repos | All returned via pagination |
| Commit author matching | Commit email matches roster | Attributed to developer |
| Commit author no match | Commit email not in roster | Skipped |
| PR author matching | PR author matches roster | Counted |
| API 401 | Invalid token | Raises auth error, doesn't crash |
| API 429 | Rate limited | Retries with backoff |
| Diffstat aggregation | Multiple files in commit | Sum of lines_added + lines_removed |

### Integration Tests

**File:** `tests/test_metrics_api.py`

| Test Case | Setup | Action | Expected |
|-----------|-------|--------|----------|
| Dashboard empty | No snapshots | GET /api/metrics/dashboard | 200, empty developers list |
| Dashboard with data | Seed 2 weeks | GET /api/metrics/dashboard | Returns current week data |
| Dashboard week navigation | Seed 4 weeks | GET /api/metrics/dashboard?week_start=... | Returns requested week |
| Report generation | Seed data | GET /api/metrics/report | 200, formatted text |
| Add developer | Valid body | POST /api/metrics/developers | 201, developer created |
| Add duplicate email | Same email | POST /api/metrics/developers | 409 conflict |
| Update developer | Change name | PATCH /api/metrics/developers/{id} | 200, updated |
| Deactivate developer | Set active=false | DELETE /api/metrics/developers/{id} | 200, soft-deleted |
| Manual collection trigger | Valid config | POST /api/metrics/collect | 200, job_id returned |
| Collection with bad BB token | Invalid token | POST /api/metrics/collect | Completes with error logged, no data overwritten |
| Collection with bad Jira token | Invalid token | POST /api/metrics/collect | Partial collection (BB succeeds, Jira fails) |

### Edge Cases (from Phase 3)

| # | Scenario | Expected Behavior | Test Type |
|---|----------|-------------------|-----------|
| 1 | Empty roster | Empty state UI with "Add Developer" CTA | E2E |
| 2 | Bitbucket 401 | Error state, log error, don't overwrite previous week | Integration |
| 3 | Jira 429 rate limit | Retry with backoff, partial collection logged | Integration |
| 4 | Developer not in Bitbucket | 0 lines / 0 PRs, still shows in dashboard | Unit |
| 5 | No tickets for developer | Show 0 across ticket/SP columns | Unit |
| 6 | Missing story points | Count ticket but 0 SP contribution | Unit |
| 7 | Week with no data | Show zeros, not skip the week | Unit |
| 8 | Duplicate collection (scheduler restart) | Upsert — overwrite same week_start + developer_id | Integration |
| 9 | 12-week rollover | Oldest week auto-deleted | Unit |
| 10 | Timezone edge (Sunday 23:59 UTC) | Use Mon–Sun boundaries in UTC consistently | Unit |
| 11 | Large repo count (50+) | Paginate Bitbucket API, aggregate across all repos | Integration |
| 12 | Developer name change | Roster update propagates; historical data preserved via FK | Integration |
| 13 | Defect trend — first week | Trend = "—" (neutral), 4-wk avg = current week | Unit |
| 14 | Cycle time = 0 (same-day close) | 0.1 day minimum (avoid division by zero in ratio) | Unit |

---

## Acceptance Criteria

### Functional

- [ ] **Given** the Metrics tab is selected, **When** data exists for the current week, **Then** all 7 dashboard cards render with correct data
- [ ] **Given** the week selector arrows are clicked, **When** historical data exists, **Then** the dashboard updates to show that week's data
- [ ] **Given** it is Monday at 07:00, **When** the scheduler fires, **Then** `collect_weekly_metrics()` runs and populates `weekly_snapshots` and `weekly_team_summary` for the previous Mon–Sun
- [ ] **Given** a developer is in the roster, **When** they have no Bitbucket commits, **Then** they appear with 0 lines and 0 PRs (not omitted)
- [ ] **Given** the report view is opened, **When** data exists, **Then** a formatted text report is displayed with a "Copy to Clipboard" button
- [ ] **Given** the manual collect button is pressed, **When** collection completes, **Then** the dashboard refreshes with new data
- [ ] **Given** defects exist in Jira, **When** the dashboard loads, **Then** the defect card shows total/new/closed counts with correct P1/P2/other breakdown
- [ ] **Given** 6+ weeks of defect history, **Then** the stacked column chart shows 6 columns with the current week highlighted
- [ ] **Given** the defect trend, **When** new bugs decreased WoW, **Then** trend shows ▼ (green, good)
- [ ] **Given** a developer's tickets, **When** stacked bars render, **Then** each segment shows its numeric value as a visible label
- [ ] **Given** EPS scores are calculated, **When** the EPS card renders, **Then** labels use: Leading (≥35), Steady (25-34), Ramping (15-24), Emerging (<15)

### Error Handling

- [ ] **Given** Bitbucket API returns 401, **When** collection runs, **Then** error is logged, previous week's data is preserved, error state shown in UI
- [ ] **Given** Jira API returns 429, **When** collection runs, **Then** retry with 60s backoff (existing pattern), log warning
- [ ] **Given** no developers in roster, **When** Metrics tab is opened, **Then** empty state with "Add Developer" prompt is shown

### Performance

- [ ] Dashboard API responds in < 500ms for 12 weeks × 10 developers
- [ ] Weekly collection completes in < 5 minutes for 50 repos × 10 developers

### UX

- [ ] Mockup approved and implementation matches visual design
- [ ] All states implemented: populated, empty, loading (skeleton), error (with retry)
- [ ] Official metrics (cycle time, lead time) are prominently visible in the week selector bar
- [ ] Stacked bar segments show inline numeric labels
- [ ] Detail table shows WoW trend with color-coded arrows (green=improving, red=degrading)

---

## Definition of Done

### Code Quality

- [ ] Code follows existing patterns in the repo (FastAPI routers, SQLAlchemy models, vanilla JS rendering)
- [ ] No new lint errors or warnings
- [ ] Bitbucket API token stored in `.env`, never logged or exposed in API responses

### Testing

- [ ] All unit tests pass (`test_metrics_collector.py`, `test_bitbucket_client.py`)
- [ ] All integration tests pass (`test_metrics_api.py`)
- [ ] Edge cases from table covered
- [ ] Manual smoke test: trigger collection, verify dashboard renders with real data

### Documentation

- [ ] `.env.example` updated with new Bitbucket + metrics config fields
- [ ] README updated with Metrics section describing setup and usage

### Review

- [ ] Self-review completed
- [ ] Manual walkthrough of all dashboard states (populated, empty, loading, error, report)

### Deployment

- [ ] App restarts cleanly with new tables auto-created
- [ ] Existing tasks/goals/agent functionality unaffected
- [ ] Scheduler registers new `weekly_metrics` job alongside existing jobs
