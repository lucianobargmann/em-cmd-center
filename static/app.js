/* EM Command Center — UI */

let currentFilter = '';
let tasks = [];
let goals = [];
let completedCollapsed = false;

// Metrics state
let metricsData = null;
let currentWeekStart = null;
let developers = [];
let activeTab = 'tasks';

// Status Board state
let sbData = null;
let sbPage = 1;
let sbSortBy = 'current_status_age';
let sbSortDir = 'desc';

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    setHeaderDate();
    loadTasks();
    loadGoals();
    loadAgentStatus();
    setupListeners();
    // Poll for updates every 60s
    setInterval(loadAgentStatus, 60000);
    setInterval(loadTasks, 60000);
    setInterval(loadGoals, 60000);
});

function setHeaderDate() {
    const el = document.getElementById('header-date');
    const d = new Date();
    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    el.textContent = `${days[d.getDay()]} · ${months[d.getMonth()]} ${d.getDate()}`;
}

function setupListeners() {
    // Add task
    document.getElementById('btn-add').addEventListener('click', addTask);
    document.getElementById('task-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addTask();
    });

    // Filters
    document.getElementById('filter-bar').addEventListener('click', (e) => {
        if (e.target.classList.contains('filter-btn')) {
            currentFilter = e.target.dataset.filter;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            loadTasks();
        }
    });

    // Clear done
    document.getElementById('btn-clear-done').addEventListener('click', clearDone);

    // Refresh
    document.getElementById('btn-refresh').addEventListener('click', triggerRefresh);

    // Report
    document.getElementById('btn-report').addEventListener('click', openReport);
    document.getElementById('report-modal-close').addEventListener('click', () => {
        document.getElementById('report-modal').classList.remove('active');
    });
    document.getElementById('report-modal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) e.currentTarget.classList.remove('active');
    });
    document.getElementById('btn-copy-report').addEventListener('click', copyReport);

    // Goals
    document.getElementById('btn-goal-history').addEventListener('click', () => {
        window.location.href = '/goals/history';
    });
    document.getElementById('btn-add-goal').addEventListener('click', () => openGoalModal());
    document.getElementById('goal-modal-close').addEventListener('click', () => {
        document.getElementById('goal-modal').classList.remove('active');
    });
    document.getElementById('goal-modal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) e.currentTarget.classList.remove('active');
    });
    document.getElementById('btn-save-goal').addEventListener('click', saveGoal);

    // Nav tabs
    document.getElementById('nav-tabs').addEventListener('click', (e) => {
        if (!e.target.classList.contains('nav-tab')) return;
        const tab = e.target.dataset.tab;
        if (tab === activeTab) return;
        activeTab = tab;
        document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        document.getElementById('tasks-view').style.display = tab === 'tasks' ? '' : 'none';
        document.getElementById('metrics-view').style.display = tab === 'metrics' ? '' : 'none';
        document.getElementById('status-board-view').style.display = tab === 'status-board' ? '' : 'none';
        if (tab === 'metrics' && !metricsData) {
            loadMetrics();
            loadDevelopers();
        }
        if (tab === 'status-board' && !sbData) {
            loadStatusBoard();
        }
    });

    // Metrics controls
    document.getElementById('week-prev').addEventListener('click', () => navigateWeek(-1));
    document.getElementById('week-next').addEventListener('click', () => navigateWeek(1));
    document.getElementById('btn-collect').addEventListener('click', triggerMetricsCollection);
    document.getElementById('btn-metrics-report').addEventListener('click', openMetricsReport);
    document.getElementById('btn-manage-roster').addEventListener('click', openRosterModal);
    document.getElementById('btn-empty-roster').addEventListener('click', openRosterModal);
    document.getElementById('btn-retry-metrics').addEventListener('click', loadMetrics);

    // Metrics report modal
    document.getElementById('metrics-report-close').addEventListener('click', () => {
        document.getElementById('metrics-report-modal').classList.remove('active');
    });
    document.getElementById('metrics-report-modal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) e.currentTarget.classList.remove('active');
    });
    document.getElementById('btn-copy-metrics-report').addEventListener('click', () => {
        const text = document.getElementById('metrics-report-text').textContent;
        navigator.clipboard.writeText(text).then(() => {
            const btn = document.getElementById('btn-copy-metrics-report');
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = 'Copy to Clipboard'; }, 2000);
        });
    });

    // Roster modal
    document.getElementById('roster-modal-close').addEventListener('click', () => {
        document.getElementById('roster-modal').classList.remove('active');
    });
    document.getElementById('roster-modal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) e.currentTarget.classList.remove('active');
    });
    document.getElementById('btn-add-dev').addEventListener('click', () => openDevEditModal(null));

    // Developer edit modal
    document.getElementById('dev-edit-modal-close').addEventListener('click', () => {
        document.getElementById('dev-edit-modal').classList.remove('active');
    });
    document.getElementById('dev-edit-modal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) e.currentTarget.classList.remove('active');
    });
    document.getElementById('btn-save-dev').addEventListener('click', saveDevEdit);

    // Merge button
    document.getElementById('btn-merge-devs').addEventListener('click', mergeSelectedDevelopers);

    // Jira user picker — server-side search, auto-fill name/email + BB auto-match
    setupJiraSearchPicker(
        document.getElementById('dev-edit-jira'),
        document.getElementById('jira-picker-edit'),
        document.getElementById('dev-edit-jira-id'),
        async (u) => {
            const nameEl = document.getElementById('dev-edit-name');
            const emailEl = document.getElementById('dev-edit-email');
            const bbEl = document.getElementById('dev-edit-bb');
            if (!nameEl.value.trim()) nameEl.value = u.label;
            if (!emailEl.value.trim() && u.email) emailEl.value = u.email;
            // Auto-match Bitbucket user by name
            if (!bbEl.value.trim()) {
                try {
                    const resp = await fetch(`/api/metrics/bitbucket-match?name=${encodeURIComponent(u.label)}`);
                    const data = await resp.json();
                    if (data.match) {
                        bbEl.value = data.match.nickname;
                        bbEl.title = `Auto-matched: ${data.match.display_name}`;
                    }
                } catch (e) { console.warn('BB auto-match failed:', e); }
            }
        }
    );

    // BB user picker — client-side filter
    setupBBSearchPicker(document.getElementById('dev-edit-bb'), document.getElementById('bb-picker-edit'));

    // Slack user picker — client-side filter
    setupSlackSearchPicker(
        document.getElementById('dev-edit-slack'),
        document.getElementById('slack-picker-edit'),
        document.getElementById('dev-edit-slack-id')
    );

    // Prevent picker clicks from closing modals
    document.querySelectorAll('.picker-dropdown').forEach(dd => {
        dd.addEventListener('click', (e) => e.stopPropagation());
        dd.addEventListener('mousedown', (e) => e.stopPropagation());
    });

    // Close pickers on outside click
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.picker-wrapper')) {
            closeAllPickers();
        }
    });

    // Status Board
    document.getElementById('btn-sb-refresh').addEventListener('click', refreshStatusBoard);
    document.getElementById('btn-sb-retry').addEventListener('click', loadStatusBoard);
    document.getElementById('btn-sb-sync-now').addEventListener('click', refreshStatusBoard);
    document.getElementById('sb-filter-project').addEventListener('change', () => { sbPage = 1; loadStatusBoard(); });
    document.getElementById('sb-filter-priority').addEventListener('change', () => { sbPage = 1; loadStatusBoard(); });
    document.getElementById('sb-filter-assignee').addEventListener('change', () => { sbPage = 1; loadStatusBoard(); });
    let sbSearchTimeout;
    document.getElementById('sb-search').addEventListener('input', (e) => {
        clearTimeout(sbSearchTimeout);
        sbSearchTimeout = setTimeout(() => { sbPage = 1; loadStatusBoard(); }, 300);
    });
}

// ---- API calls ----
async function loadTasks() {
    const url = currentFilter ? `/api/tasks?filter=${currentFilter}` : '/api/tasks';
    try {
        const resp = await fetch(url);
        tasks = await resp.json();
        // Skip re-render if a panel is open to avoid destroying it
        if (openPanelTaskId) return;
        renderTasks();
    } catch (e) {
        console.error('Failed to load tasks:', e);
    }
}

async function addTask() {
    const input = document.getElementById('task-input');
    const title = input.value.trim();
    if (!title) return;

    const priority = document.getElementById('priority-select').value;
    const category = document.getElementById('category-select').value;

    try {
        await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, priority, category }),
        });
        input.value = '';
        loadTasks();
    } catch (e) {
        console.error('Failed to add task:', e);
    }
}

async function toggleTask(id) {
    try {
        await fetch(`/api/tasks/${id}/toggle`, { method: 'POST' });
        loadTasks();
    } catch (e) {
        console.error('Failed to toggle task:', e);
    }
}

async function deleteTask(id) {
    try {
        await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
        loadTasks();
    } catch (e) {
        console.error('Failed to delete task:', e);
    }
}

async function clearDone() {
    try {
        await fetch('/api/tasks/done', { method: 'DELETE' });
        loadTasks();
    } catch (e) {
        console.error('Failed to clear done:', e);
    }
}

async function triggerRefresh() {
    const btn = document.getElementById('btn-refresh');
    btn.classList.add('spinning');

    try {
        const resp = await fetch('/api/agent/run', { method: 'POST' });
        const data = await resp.json();
        const jobId = data.job_id;

        // Poll until done
        const poll = setInterval(async () => {
            try {
                const r = await fetch(`/api/agent/run/${jobId}`);
                const s = await r.json();
                if (s.status === 'done' || s.status === 'error') {
                    clearInterval(poll);
                    btn.classList.remove('spinning');
                    loadTasks();
                    loadAgentStatus();
                }
            } catch {
                clearInterval(poll);
                btn.classList.remove('spinning');
            }
        }, 2000);
    } catch (e) {
        btn.classList.remove('spinning');
        console.error('Failed to trigger refresh:', e);
    }
}

async function loadAgentStatus() {
    try {
        const resp = await fetch('/api/agent/status');
        const data = await resp.json();
        const dot = document.getElementById('agent-dot');

        dot.className = 'agent-dot ' + (data.indicator || 'amber');

        // Tooltip
        let tip = dot.querySelector('.tooltip');
        if (!tip) {
            tip = document.createElement('span');
            tip.className = 'tooltip';
            dot.appendChild(tip);
        }

        if (data.last_run) {
            const d = new Date(data.last_run);
            tip.textContent = `Last: ${d.toLocaleTimeString()} — ${data.status}`;
        } else {
            tip.textContent = 'No agent runs yet';
        }
    } catch (e) {
        console.error('Failed to load agent status:', e);
    }
}

// ---- Goals ----
async function loadGoals() {
    try {
        const resp = await fetch('/api/goals');
        goals = await resp.json();
        renderGoals();
    } catch (e) {
        console.error('Failed to load goals:', e);
    }
}

function renderGoals() {
    const list = document.getElementById('goals-list');
    const label = document.getElementById('goals-week-label');
    list.innerHTML = '';

    const activeGoals = goals.filter(g => g.status !== 'archived');
    const count = activeGoals.length;

    if (goals.length > 0) {
        const ws = goals[0].week_start;
        if (ws) {
            const d = new Date(ws + 'T00:00:00');
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            label.textContent = `CTO Goals (Week of ${months[d.getMonth()]} ${d.getDate()}) - ${count}`;
        }
    } else {
        label.textContent = 'CTO Goals';
    }

    for (const g of goals) {
        if (g.status === 'archived') continue;
        const row = document.createElement('div');
        row.className = 'goal-row';
        row.draggable = true;
        row.dataset.goalId = g.id;

        // Drag handle
        const handle = document.createElement('span');
        handle.className = 'goal-drag-handle';
        handle.textContent = '\u2630';
        handle.title = 'Drag to reorder';
        row.appendChild(handle);

        // Drag events
        row.addEventListener('dragstart', (e) => {
            row.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', g.id);
        });
        row.addEventListener('dragend', () => {
            row.classList.remove('dragging');
            document.querySelectorAll('.goal-row.drag-over').forEach(r => r.classList.remove('drag-over'));
        });
        row.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            const dragging = list.querySelector('.dragging');
            if (dragging && dragging !== row) {
                row.classList.add('drag-over');
            }
        });
        row.addEventListener('dragleave', () => {
            row.classList.remove('drag-over');
        });
        row.addEventListener('drop', (e) => {
            e.preventDefault();
            row.classList.remove('drag-over');
            const draggedId = e.dataTransfer.getData('text/plain');
            if (draggedId && draggedId !== g.id) {
                reorderGoal(draggedId, g.id);
            }
        });

        // Status icon (clickable to cycle)
        const icon = document.createElement('span');
        icon.className = 'goal-status-icon';
        icon.textContent = g.status === 'completed' ? '\u2705' : '\uD83D\uDD35';
        icon.title = `Status: ${g.status} (click to cycle)`;
        icon.addEventListener('click', () => cycleGoalStatus(g));
        row.appendChild(icon);

        // Percent complete indicator
        const pct = g.percent_complete || 0;
        if (pct > 0 && g.status !== 'completed') {
            const pctEl = document.createElement('span');
            pctEl.className = 'goal-pct';
            pctEl.textContent = pct + '%';
            pctEl.title = pct + '% complete';
            row.appendChild(pctEl);
        }

        // Title
        const title = document.createElement('span');
        title.className = 'goal-title' + (g.status === 'completed' ? ' done' : '');
        title.textContent = g.title;
        title.addEventListener('click', () => openGoalModal(g));
        row.appendChild(title);

        // Latest progress note (read-only)
        const pn = g.progress_notes || [];
        const latestText = document.createElement('span');
        latestText.className = 'goal-latest-note';
        latestText.textContent = pn.length > 0 ? pn[0].text : '';
        latestText.title = pn.length > 1 ? `${pn.length} notes — click goal title to see all` : '';
        row.appendChild(latestText);

        // Inline add-note input
        const noteInput = document.createElement('input');
        noteInput.className = 'goal-notes-inline';
        noteInput.type = 'text';
        noteInput.value = '';
        noteInput.placeholder = '+ note...';
        noteInput.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                const text = noteInput.value.trim();
                if (!text) return;
                noteInput.value = '';
                await addGoalNote(g.id, text);
            }
        });
        row.appendChild(noteInput);

        // Jira link
        if (g.jira_key && g.jira_url) {
            const link = document.createElement('a');
            link.className = 'jira-link';
            link.href = g.jira_url;
            link.target = '_blank';
            link.rel = 'noopener';
            link.textContent = g.jira_key;
            row.appendChild(link);
        }

        // Delete (archive)
        const del = document.createElement('button');
        del.className = 'btn-delete';
        del.textContent = '\u00d7';
        del.addEventListener('click', (e) => {
            e.stopPropagation();
            archiveGoal(g.id);
        });
        row.appendChild(del);

        list.appendChild(row);
    }
}

async function cycleGoalStatus(goal) {
    const next = goal.status === 'active' ? 'completed' : 'active';
    try {
        await fetch(`/api/goals/${goal.id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: next }),
        });
        loadGoals();
    } catch (e) {
        console.error('Failed to cycle goal status:', e);
    }
}

async function addGoalNote(goalId, text) {
    try {
        await fetch(`/api/goals/${goalId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ progress_note: text }),
        });
        loadGoals();
    } catch (e) {
        console.error('Failed to add goal note:', e);
    }
}

async function archiveGoal(goalId) {
    try {
        await fetch(`/api/goals/${goalId}`, { method: 'DELETE' });
        loadGoals();
    } catch (e) {
        console.error('Failed to archive goal:', e);
    }
}

async function reorderGoal(draggedId, targetId) {
    const rows = document.querySelectorAll('.goal-row[data-goal-id]');
    const ids = [...rows].map(r => r.dataset.goalId);
    const fromIdx = ids.indexOf(draggedId);
    const toIdx = ids.indexOf(targetId);
    if (fromIdx === -1 || toIdx === -1) return;

    ids.splice(fromIdx, 1);
    ids.splice(toIdx, 0, draggedId);

    try {
        await Promise.all(ids.map((id, i) =>
            fetch(`/api/goals/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sort_order: i }),
            })
        ));
        loadGoals();
    } catch (e) {
        console.error('Failed to reorder goals:', e);
    }
}

function openGoalModal(goal) {
    const modal = document.getElementById('goal-modal');
    document.getElementById('goal-modal-title').textContent = goal ? 'Edit Goal' : 'Add Goal';
    document.getElementById('goal-edit-id').value = goal ? goal.id : '';
    document.getElementById('goal-title-input').value = goal ? goal.title : '';
    document.getElementById('goal-desc-input').value = goal ? (goal.description || '') : '';
    document.getElementById('goal-notes-input').value = '';
    document.getElementById('goal-notes-input').placeholder = goal ? 'Add a progress note...' : 'Initial progress note...';

    const pctInput = document.getElementById('goal-pct-input');
    const pctLabel = document.getElementById('goal-pct-label');
    const pctVal = goal ? (goal.percent_complete || 0) : 0;
    pctInput.value = pctVal;
    pctLabel.textContent = pctVal + '%';
    pctInput.oninput = () => { pctLabel.textContent = pctInput.value + '%'; };

    // Render note history
    const historyEl = document.getElementById('goal-notes-history');
    historyEl.innerHTML = '';
    if (goal && goal.progress_notes && goal.progress_notes.length > 0) {
        for (const n of goal.progress_notes) {
            const entry = document.createElement('div');
            entry.className = 'note-entry';
            const ts = new Date(n.ts);
            const timeStr = ts.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                + ' ' + ts.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
            entry.innerHTML = `<span class="note-ts">${timeStr}</span> ${escapeHtml(n.text)}`;
            historyEl.appendChild(entry);
        }
    }

    modal.classList.add('active');
    if (goal) {
        document.getElementById('goal-notes-input').focus();
    } else {
        document.getElementById('goal-title-input').focus();
    }
}

function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

async function saveGoal() {
    const id = document.getElementById('goal-edit-id').value;
    const title = document.getElementById('goal-title-input').value.trim();
    if (!title) return;

    const noteText = document.getElementById('goal-notes-input').value.trim() || null;
    const pct = parseInt(document.getElementById('goal-pct-input').value) || 0;

    try {
        if (id) {
            const payload = {
                title,
                description: document.getElementById('goal-desc-input').value.trim() || null,
                percent_complete: pct,
            };
            if (noteText) payload.progress_note = noteText;
            await fetch(`/api/goals/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } else {
            const payload = {
                title,
                description: document.getElementById('goal-desc-input').value.trim() || null,
            };
            if (noteText) payload.progress_note = noteText;
            await fetch('/api/goals', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        }
        document.getElementById('goal-modal').classList.remove('active');
        loadGoals();
    } catch (e) {
        console.error('Failed to save goal:', e);
    }
}

// ---- Report ----
async function openReport() {
    const modal = document.getElementById('report-modal');
    const textEl = document.getElementById('report-text');
    textEl.textContent = 'Loading...';
    modal.classList.add('active');

    try {
        const resp = await fetch('/api/reports/daily');
        const data = await resp.json();
        textEl.textContent = data.slack_text;
    } catch (e) {
        textEl.textContent = 'Failed to load report.';
        console.error('Failed to load report:', e);
    }
}

async function copyReport() {
    const text = document.getElementById('report-text').textContent;
    try {
        await navigator.clipboard.writeText(text);
        const btn = document.getElementById('btn-copy-report');
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy to Clipboard'; }, 2000);
    } catch (e) {
        console.error('Failed to copy:', e);
    }
}

// ---- Rendering ----
const PRIORITY_LABELS = {
    p1: 'P1 - CEO ESCALATION',
    p2: 'P2 - COMPANY PRIORITY',
    p3: 'P3 - THIS WEEK',
    p4: 'P4 - BACKLOG',
};

function renderTasks() {
    const container = document.getElementById('task-list');
    container.innerHTML = '';

    const open = tasks.filter(t => !t.done);
    const done = tasks.filter(t => t.done);

    // Update progress
    const total = tasks.length;
    const doneCount = done.length;
    document.getElementById('progress-pill').textContent = `${doneCount}/${total} done`;

    // Group open tasks by priority
    const groups = {};
    for (const t of open) {
        const p = t.priority;
        if (!groups[p]) groups[p] = [];
        groups[p].push(t);
    }

    let firstP1Shown = false;

    for (const pri of ['p1', 'p2', 'p3', 'p4']) {
        const items = groups[pri];
        if (!items || items.length === 0) continue;

        // Priority header
        const header = document.createElement('div');
        header.className = 'priority-header';
        header.textContent = PRIORITY_LABELS[pri] || pri.toUpperCase();
        container.appendChild(header);

        for (const task of items) {
            const isDoNext = pri === 'p1' && !firstP1Shown;
            if (isDoNext) firstP1Shown = true;
            container.appendChild(createTaskRow(task, isDoNext));
        }
    }

    // Completed section
    if (done.length > 0) {
        const section = document.createElement('div');
        section.className = 'completed-section' + (completedCollapsed ? ' collapsed' : '');

        const header = document.createElement('div');
        header.className = 'completed-header';
        header.innerHTML = `<span class="toggle ${completedCollapsed ? 'collapsed' : ''}">▼</span> COMPLETED (${done.length})`;
        header.addEventListener('click', () => {
            completedCollapsed = !completedCollapsed;
            section.classList.toggle('collapsed');
            header.querySelector('.toggle').classList.toggle('collapsed');
        });
        section.appendChild(header);

        for (const task of done) {
            section.appendChild(createTaskRow(task, false));
        }
        container.appendChild(section);
    }

    // Re-open panel if one was active before re-render
    if (openPanelTaskId && openPanelType) {
        const savedId = openPanelTaskId;
        const savedType = openPanelType;
        const wrapper = document.querySelector(`.task-wrapper[data-task-id="${savedId}"]`);
        if (wrapper && !wrapper.querySelector('.detail-panel')) {
            // Only re-open non-streaming panels (analysis/ranking) since comment is long-running
            if (savedType !== 'comment') {
                openPanelTaskId = null;
                openPanelType = null;
                togglePanel(savedId, savedType);
            } else {
                // For comment panel, just keep the state but don't re-trigger
                openPanelTaskId = null;
                openPanelType = null;
            }
        }
    }
}

function createTaskRow(task, isDoNext) {
    const row = document.createElement('div');
    row.className = 'task-row';

    // Checkbox
    const cb = document.createElement('div');
    cb.className = 'task-checkbox' + (task.done ? ' checked' : '');
    cb.addEventListener('click', () => toggleTask(task.id));
    row.appendChild(cb);

    // DO NEXT badge
    if (isDoNext) {
        const badge = document.createElement('span');
        badge.className = 'do-next-badge';
        badge.textContent = 'DO NEXT';
        row.appendChild(badge);
    }

    // Title
    const title = document.createElement('span');
    title.className = 'task-title' + (task.done ? ' done' : '');
    title.textContent = task.title;
    title.title = task.notes || task.title;
    row.appendChild(title);

    // Priority badge
    const priBadge = document.createElement('span');
    priBadge.className = `badge badge-${task.priority}`;
    priBadge.textContent = task.priority.toUpperCase();
    row.appendChild(priBadge);

    // Category badge
    const catBadge = document.createElement('span');
    catBadge.className = 'badge badge-cat';
    catBadge.textContent = task.category;
    row.appendChild(catBadge);

    // Auto badge
    if (task.auto) {
        const autoBadge = document.createElement('span');
        autoBadge.className = 'badge badge-auto';
        autoBadge.textContent = 'auto';
        row.appendChild(autoBadge);
    }

    // Jira link
    if (task.jira_key && task.jira_url) {
        const link = document.createElement('a');
        link.className = 'jira-link';
        link.href = task.jira_url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = task.jira_key;
        row.appendChild(link);
    }

    // Reviewed indicator (always visible)
    if (task.jira_key && task.reviewed_at) {
        const reviewedIcon = document.createElement('span');
        reviewedIcon.className = 'reviewed-icon';
        reviewedIcon.title = 'Up to date';
        reviewedIcon.textContent = '\u2714';
        row.appendChild(reviewedIcon);
    }

    // Action buttons container
    const actions = document.createElement('span');
    actions.className = 'task-actions';

    // Mark reviewed button (Jira-linked tasks)
    if (task.jira_key) {
        const reviewBtn = document.createElement('button');
        reviewBtn.className = 'btn-action' + (task.reviewed_at ? ' reviewed' : '');
        reviewBtn.textContent = '\u2714';
        reviewBtn.title = task.reviewed_at ? 'Mark as not reviewed' : 'Mark as reviewed';
        reviewBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            markReviewed(task.id);
        });
        actions.appendChild(reviewBtn);
    }

    // AI Analysis button (only for Jira-linked tasks)
    if (task.jira_key) {
        const analysisBtn = document.createElement('button');
        analysisBtn.className = 'btn-action';
        analysisBtn.textContent = '?';
        analysisBtn.title = 'AI Analysis';
        analysisBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePanel(task.id, 'analysis');
        });
        actions.appendChild(analysisBtn);

        // Suggest Comment button
        const commentBtn = document.createElement('button');
        commentBtn.className = 'btn-action';
        commentBtn.innerHTML = '&#x1F4AC;';
        commentBtn.title = 'Suggest Comment';
        commentBtn.style.fontSize = '13px';
        commentBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePanel(task.id, 'comment');
        });
        actions.appendChild(commentBtn);
    }

    // Ranking Rationale button (all tasks)
    const rankBtn = document.createElement('button');
    rankBtn.className = 'btn-action';
    rankBtn.textContent = 'i';
    rankBtn.title = 'Ranking Rationale';
    rankBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        togglePanel(task.id, 'ranking');
    });
    actions.appendChild(rankBtn);

    row.appendChild(actions);

    // Delete button
    const del = document.createElement('button');
    del.className = 'btn-delete';
    del.textContent = '\u00d7';
    del.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteTask(task.id);
    });
    row.appendChild(del);

    // Wrapper to hold row + expandable panel
    const wrapper = document.createElement('div');
    wrapper.className = 'task-wrapper';
    wrapper.dataset.taskId = task.id;
    wrapper.appendChild(row);

    return wrapper;
}

// ---- Detail Panels ----
let openPanelTaskId = null;
let openPanelType = null;
const CLOSE_BTN = '<button class="btn-close-panel" onclick="closePanel()">&times;</button>';

function closePanel() {
    document.querySelectorAll('.detail-panel').forEach(p => p.remove());
    openPanelTaskId = null;
    openPanelType = null;
}

function togglePanel(taskId, type) {
    const wrapper = document.querySelector(`.task-wrapper[data-task-id="${taskId}"]`);
    if (!wrapper) return;

    // If same panel was open, close it
    if (openPanelTaskId === taskId && openPanelType === type) {
        closePanel();
        return;
    }

    // Close any existing panel first
    document.querySelectorAll('.detail-panel').forEach(p => p.remove());

    openPanelTaskId = taskId;
    openPanelType = type;

    const panel = document.createElement('div');
    panel.className = 'detail-panel';
    panel.innerHTML = CLOSE_BTN + '<div class="detail-loading">Loading...</div>';
    wrapper.appendChild(panel);

    if (type === 'analysis') {
        showAnalysis(taskId, panel);
    } else if (type === 'comment') {
        showCommentSuggest(taskId, panel);
    } else {
        showRanking(taskId, panel);
    }
}

async function showAnalysis(taskId, panel) {
    try {
        const resp = await fetch(`/api/tasks/${taskId}/analysis`);
        if (!resp.ok) {
            const err = await resp.json();
            panel.innerHTML = CLOSE_BTN + `<div class="detail-error">${escapeHtml(err.detail || 'Failed to load')}</div>`;
            return;
        }
        const data = await resp.json();
        const f = data.fields;

        let html = '<div class="detail-content">';
        html += `<div class="detail-summary">${escapeHtml(data.summary)}</div>`;

        html += '<div class="detail-fields">';
        html += fieldRow('Status', f.status);
        html += fieldRow('Assignee', f.assignee || 'Unassigned');
        html += fieldRow('Priority', f.priority);
        html += fieldRow('Story Points', f.story_points ?? 'None');
        html += fieldRow('Age', f.age_days != null ? `${f.age_days} days` : 'N/A');
        html += fieldRow('Stale', f.stale_days != null ? `${f.stale_days} days since update` : 'N/A');
        html += fieldRow('Comments', f.comments_count);
        if (f.due_date) {
            const src = f.due_date_source === 'fixVersion' ? ' (from fixVersion)' : '';
            html += fieldRow('Due', f.due_date + src);
        }
        if (f.fix_versions && f.fix_versions.length > 0) {
            html += fieldRow('Fix Version', f.fix_versions.join(', '));
        }
        if (f.blockers && f.blockers.length > 0) {
            const blockerStr = f.blockers.map(b => `${b.key} (${b.status})`).join(', ');
            html += fieldRow('Blockers', blockerStr);
        }
        html += '</div>';

        if (f.description_snippet) {
            html += `<div class="detail-desc">${escapeHtml(f.description_snippet)}</div>`;
        }

        html += '<div class="detail-actions-header">Suggested Actions</div>';
        html += '<ul class="detail-actions-list">';
        for (const action of data.actions) {
            html += `<li>${escapeHtml(action)}</li>`;
        }
        html += '</ul>';
        html += '</div>';

        panel.innerHTML = CLOSE_BTN + html;
    } catch (e) {
        panel.innerHTML = CLOSE_BTN + '<div class="detail-error">Failed to load analysis</div>';
        console.error('Analysis error:', e);
    }
}

async function showRanking(taskId, panel) {
    try {
        const resp = await fetch(`/api/tasks/${taskId}/ranking`);
        if (!resp.ok) {
            const err = await resp.json();
            panel.innerHTML = CLOSE_BTN + `<div class="detail-error">${escapeHtml(err.detail || 'Failed to load')}</div>`;
            return;
        }
        const data = await resp.json();
        const f = data.factors;

        let html = '<div class="detail-content">';
        html += `<div class="detail-summary">${escapeHtml(data.explanation)}</div>`;

        html += '<div class="detail-fields">';
        html += fieldRow('Priority', f.priority_label);
        html += fieldRow('Source', f.source + (f.auto ? ' (auto)' : ''));
        html += fieldRow('Category', f.category);
        html += fieldRow('Age', f.age_days != null ? `${f.age_days} days` : 'N/A');
        html += fieldRow('Jira', f.has_jira_link ? (f.jira_key || 'Yes') : 'None');
        if (f.gap_type) html += fieldRow('Gap Type', f.gap_type);
        if (data.position) html += fieldRow('Position', `#${data.position} of ${data.open_count}`);
        html += '</div>';

        html += `<div class="detail-sort-explanation">${escapeHtml(data.sort_explanation)}</div>`;
        html += '</div>';

        panel.innerHTML = CLOSE_BTN + html;
    } catch (e) {
        panel.innerHTML = CLOSE_BTN + '<div class="detail-error">Failed to load ranking</div>';
        console.error('Ranking error:', e);
    }
}

function showCommentSuggest(taskId, panel) {
    panel.innerHTML = CLOSE_BTN + '<div class="detail-content"><div class="detail-loading" id="comment-steps-' + taskId + '">Connecting...</div></div>';

    const es = new EventSource(`/api/tasks/${taskId}/suggest-comment`);
    const stepsEl = document.getElementById(`comment-steps-${taskId}`);
    const steps = [];

    es.addEventListener('step', (e) => {
        const data = JSON.parse(e.data);
        console.log(`[Duke] ${data.step}`);
        steps.push(data.step);
        let html = steps.map((s, i) => {
            const done = i < steps.length - 1;
            const icon = done ? '<span style="color:var(--green)">&#10003;</span>' : '<span class="detail-loading">&#9679;</span>';
            return `<div style="font-size:12px;padding:2px 0;color:${done ? 'var(--text-secondary)' : 'var(--text)'}">${icon} ${escapeHtml(s)}</div>`;
        }).join('');
        stepsEl.innerHTML = html;
    });

    es.addEventListener('done', (e) => {
        es.close();
        const data = JSON.parse(e.data);
        console.log('[Duke] Comment suggestion complete', data);

        if (data.error) {
            panel.innerHTML = CLOSE_BTN + `<div class="detail-error">${escapeHtml(data.error)}</div>`;
            return;
        }

        let html = '<div class="detail-content">';
        html += `<div class="detail-summary">${escapeHtml(data.jira_key)} - ${data.comments_count} existing comment(s)</div>`;
        html += `<div class="detail-desc">${escapeHtml(data.summary)}</div>`;
        html += '<div class="detail-actions-header">Suggested Comment</div>';
        html += `<textarea class="comment-textarea" id="comment-text-${taskId}">${escapeHtml(data.suggested_comment)}</textarea>`;
        html += '<div style="display:flex;gap:6px;margin-top:6px;align-items:center">';
        html += `<button class="btn-send-comment" onclick="postComment('${taskId}')">Send to Jira</button>`;
        html += `<button class="btn-copy-comment" onclick="copyComment('${taskId}')">Copy</button>`;
        html += `<div class="comment-status" id="comment-status-${taskId}"></div>`;
        html += '</div>';
        html += '</div>';
        panel.innerHTML = CLOSE_BTN + html;
    });

    es.addEventListener('error', (e) => {
        es.close();
        if (e.data) {
            const data = JSON.parse(e.data);
            console.error('[Duke] SSE error:', data.detail);
            panel.innerHTML = CLOSE_BTN + `<div class="detail-error">${escapeHtml(data.detail)}</div>`;
        } else {
            console.error('[Duke] Connection lost');
            panel.innerHTML = CLOSE_BTN + '<div class="detail-error">Connection lost - try again</div>';
        }
    });

    es.onerror = () => {
        es.close();
        if (!panel.querySelector('.detail-desc') && !panel.querySelector('.detail-error')) {
            console.error('[Duke] EventSource failed');
            panel.innerHTML = CLOSE_BTN + '<div class="detail-error">Connection failed - try again</div>';
        }
    };
}

async function postComment(taskId) {
    const textarea = document.getElementById(`comment-text-${taskId}`);
    const status = document.getElementById(`comment-status-${taskId}`);
    const btn = textarea.parentElement.querySelector('.btn-send-comment');
    const comment = textarea.value.trim();

    if (!comment) {
        status.textContent = 'Comment cannot be empty';
        status.className = 'comment-status error';
        return;
    }

    btn.disabled = true;
    status.textContent = 'Posting...';
    status.className = 'comment-status';

    try {
        const resp = await fetch(`/api/tasks/${taskId}/post-comment`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comment }),
        });
        const data = await resp.json();

        if (resp.ok && data.success) {
            status.textContent = `Comment posted to ${data.jira_key}`;
            status.className = 'comment-status success';
            textarea.disabled = true;
            // Auto-mark as reviewed
            markReviewed(taskId, true);
        } else {
            status.textContent = data.detail || 'Failed to post comment';
            status.className = 'comment-status error';
            btn.disabled = false;
        }
    } catch (e) {
        status.textContent = 'Network error posting comment';
        status.className = 'comment-status error';
        btn.disabled = false;
        console.error('Post comment error:', e);
    }
}

function copyComment(taskId) {
    const textarea = document.getElementById(`comment-text-${taskId}`);
    const status = document.getElementById(`comment-status-${taskId}`);
    navigator.clipboard.writeText(textarea.value).then(() => {
        status.textContent = 'Copied!';
        status.className = 'comment-status success';
        setTimeout(() => { status.textContent = ''; }, 2000);
    }).catch(() => {
        textarea.select();
        document.execCommand('copy');
        status.textContent = 'Copied!';
        status.className = 'comment-status success';
        setTimeout(() => { status.textContent = ''; }, 2000);
    });
}

async function markReviewed(taskId, forceOn) {
    // If forceOn and already reviewed, skip (avoids toggling off)
    const existing = tasks.find(t => t.id === taskId);
    if (forceOn && existing && existing.reviewed_at) return;

    try {
        const resp = await fetch(`/api/tasks/${taskId}/mark-reviewed`, { method: 'POST' });
        if (resp.ok) {
            const data = await resp.json();
            const task = tasks.find(t => t.id === taskId);
            if (task) {
                task.reviewed_at = data.reviewed ? data.reviewed_at : null;
                renderTasks();
            }
        }
    } catch (e) {
        console.error('Mark reviewed error:', e);
    }
}

function fieldRow(label, value) {
    return `<div class="detail-field"><span class="detail-label">${escapeHtml(label)}</span><span class="detail-value">${escapeHtml(String(value))}</span></div>`;
}

// ===== Metrics Dashboard =====

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function formatWeekLabel(isoDate) {
    const d = new Date(isoDate + 'T00:00:00');
    return `Week of ${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

async function loadMetrics() {
    showMetricsState('loading');
    const url = currentWeekStart
        ? `/api/metrics/dashboard?week_start=${currentWeekStart}`
        : '/api/metrics/dashboard';
    try {
        const resp = await fetch(url);
        metricsData = await resp.json();
        currentWeekStart = metricsData.week_start;

        if (!metricsData.developers || metricsData.developers.length === 0) {
            // Check if we have any developers in roster
            if (developers.length === 0) {
                try {
                    const dr = await fetch('/api/metrics/developers');
                    developers = await dr.json();
                } catch {}
            }
            if (developers.length === 0) {
                showMetricsState('empty');
                return;
            }
        }
        showMetricsState('data');
        renderMetrics();
    } catch (e) {
        console.error('Failed to load metrics:', e);
        showMetricsState('error');
    }
}

async function loadDevelopers() {
    try {
        const resp = await fetch('/api/metrics/developers');
        developers = await resp.json();
    } catch (e) {
        console.error('Failed to load developers:', e);
    }
}

function showMetricsState(state) {
    document.getElementById('metrics-loading').style.display = state === 'loading' ? '' : 'none';
    document.getElementById('metrics-empty').style.display = state === 'empty' ? '' : 'none';
    document.getElementById('metrics-error').style.display = state === 'error' ? '' : 'none';
    document.getElementById('cards-grid').style.display = state === 'data' ? '' : 'none';
}

function navigateWeek(delta) {
    if (!metricsData) return;
    const weeks = metricsData.weeks_available || [];
    if (weeks.length === 0) return;
    const idx = weeks.indexOf(currentWeekStart);
    // weeks are sorted descending (newest first)
    const newIdx = idx - delta; // -1 = newer, +1 = older
    if (newIdx >= 0 && newIdx < weeks.length) {
        currentWeekStart = weeks[newIdx];
        loadMetrics();
    }
}

function renderMetrics() {
    if (!metricsData) return;
    const data = metricsData;

    // Week label
    document.getElementById('metrics-week-label').textContent = formatWeekLabel(data.week_start);

    // Official metrics — All Tickets
    const om = data.official_metrics;
    const ctEl = document.getElementById('official-ct');
    const ltEl = document.getElementById('official-lt');
    ctEl.textContent = om.avg_cycle_time != null ? `${om.avg_cycle_time}d` : '--';
    ltEl.textContent = om.avg_lead_time != null ? `${om.avg_lead_time}d` : '--';
    document.getElementById('all-issues-count').textContent = om.all_issues_count ? `n=${om.all_issues_count}` : '';
    appendDelta(ctEl, om.avg_cycle_time, om.prev_cycle_time, true);
    appendDelta(ltEl, om.avg_lead_time, om.prev_lead_time, true);

    // Official metrics — Roster Avg
    const rctEl = document.getElementById('roster-ct');
    const rltEl = document.getElementById('roster-lt');
    rctEl.textContent = om.roster_avg_cycle_time != null ? `${om.roster_avg_cycle_time}d` : '--';
    rltEl.textContent = om.roster_avg_lead_time != null ? `${om.roster_avg_lead_time}d` : '--';
    document.getElementById('roster-issues-count').textContent = om.roster_issues_count ? `n=${om.roster_issues_count}` : '';
    appendDelta(rctEl, om.roster_avg_cycle_time, om.prev_roster_cycle_time, true);
    appendDelta(rltEl, om.roster_avg_lead_time, om.prev_roster_lead_time, true);

    // Cards
    renderBarChart('chart-lines', data.developers, 'lines_committed', 'var(--accent)');
    renderBarChart('chart-prs', data.developers, 'pr_count', 'var(--purple)');
    renderStackedBars('chart-tickets', data.developers, 'tickets');
    renderStackedBars('chart-sp', data.developers, 'story_points');
    renderRatioChart('chart-ratio', data.developers);
    renderDefectSummaryCard('chart-defects-summary', data.defects, data.defect_history);
    renderDefectPriorityCard('chart-defects-priority', data.defects, data.defect_priority_history || []);
    renderEPSCard('chart-eps', data.developers);
    renderDetailTable('detail-table-container', data.developers);
}

function appendDelta(parentEl, current, prev, lowerIsBetter) {
    // Remove existing delta
    const existing = parentEl.parentElement.querySelector('.metric-delta');
    if (existing) existing.remove();

    if (current == null || prev == null) return;
    const delta = current - prev;
    if (Math.abs(delta) < 0.05) return;

    const span = document.createElement('span');
    const arrow = delta < 0 ? '\u25BC' : '\u25B2';
    const isGood = lowerIsBetter ? delta < 0 : delta > 0;
    span.className = `metric-delta ${isGood ? 'good' : 'bad'}`;
    span.textContent = `${arrow} ${Math.abs(delta).toFixed(1)}`;
    parentEl.parentElement.appendChild(span);
}

function renderBarChart(containerId, devs, field, color) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!devs || devs.length === 0) {
        container.textContent = 'No data';
        return;
    }

    const maxVal = Math.max(...devs.map(d => d[field] || 0), 1);
    const chart = document.createElement('div');
    chart.className = 'bar-chart';

    for (const d of devs) {
        const val = d[field] || 0;
        const pct = (val / maxVal * 100).toFixed(1);
        const row = document.createElement('div');
        row.className = 'bar-row';
        row.innerHTML = `
            <span class="bar-label">${escapeHtml(d.name)}</span>
            <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
            <span class="bar-value">${val.toLocaleString()}</span>
        `;
        chart.appendChild(row);
    }
    container.appendChild(chart);
}

function renderStackedBars(containerId, devs, field) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!devs || devs.length === 0) { container.textContent = 'No data'; return; }

    const buckets = ['todo', 'wip', 'qa', 'closed'];
    const maxTotal = Math.max(...devs.map(d => {
        const obj = d[field] || {};
        return buckets.reduce((s, b) => s + (obj[b] || 0), 0);
    }), 1);

    const chart = document.createElement('div');
    chart.className = 'stacked-bar-chart';

    for (const d of devs) {
        const obj = d[field] || {};
        const total = buckets.reduce((s, b) => s + (obj[b] || 0), 0);
        const row = document.createElement('div');
        row.className = 'stacked-row';

        let barHtml = `<span class="bar-label">${escapeHtml(d.name)}</span><div class="stacked-bar">`;
        for (const b of buckets) {
            const val = obj[b] || 0;
            if (val === 0) continue;
            const pct = (val / maxTotal * 100).toFixed(1);
            barHtml += `<div class="seg seg-${b}" style="width:${pct}%"><span class="seg-label">${val}</span></div>`;
        }
        barHtml += `</div><span class="bar-value">${total}</span>`;
        row.innerHTML = barHtml;
        chart.appendChild(row);
    }

    // Legend
    const legend = document.createElement('div');
    legend.className = 'stacked-legend';
    legend.innerHTML = `
        <span class="legend-item"><span class="legend-dot" style="background:var(--text-secondary)"></span>TODO</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--blue)"></span>WIP</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--amber)"></span>QA</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--green)"></span>Done</span>
    `;
    chart.appendChild(legend);
    container.appendChild(chart);
}

function renderRatioChart(containerId, devs) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!devs || devs.length === 0) { container.textContent = 'No data'; return; }

    const maxVal = Math.max(...devs.map(d => d.sp_per_day || 0), 1);
    const chart = document.createElement('div');
    chart.className = 'bar-chart';

    for (const d of devs) {
        const val = d.sp_per_day || 0;
        const pct = (val / maxVal * 100).toFixed(1);
        const row = document.createElement('div');
        row.className = 'bar-row';
        row.innerHTML = `
            <span class="bar-label">${escapeHtml(d.name)}</span>
            <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:var(--green)"></div></div>
            <span class="bar-value">${val.toFixed(1)}</span>
        `;
        chart.appendChild(row);
    }
    container.appendChild(chart);
}

function renderDefectSummaryCard(containerId, defects, history) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';

    // Summary row
    const summary = document.createElement('div');
    summary.className = 'defect-summary';

    const stats = [
        { label: 'Open', value: defects.total, url: defects.jira_open_url },
        { label: 'New', value: defects.new, url: defects.jira_new_url },
        { label: 'Closed', value: defects.closed, url: defects.jira_closed_url },
    ];
    for (const s of stats) {
        if (s.url) {
            summary.innerHTML += `<a class="defect-stat defect-stat-link" href="${escapeHtml(s.url)}" target="_blank" rel="noopener"><span class="defect-stat-value">${s.value}</span><span class="defect-stat-label">${s.label}</span></a>`;
        } else {
            summary.innerHTML += `<div class="defect-stat"><span class="defect-stat-value">${s.value}</span><span class="defect-stat-label">${s.label}</span></div>`;
        }
    }

    // Trend -- based on open defect count change WoW
    let trendArrow = '-';
    let trendClass = 'neutral';
    if (defects.trend === 'up') { trendArrow = '\u25B2 +' + Math.abs(defects.wow_delta); trendClass = 'bad'; }
    else if (defects.trend === 'down') { trendArrow = '\u25BC -' + Math.abs(defects.wow_delta); trendClass = 'good'; }
    const netFlow = defects.net_flow != null ? defects.net_flow : 0;
    const netSign = netFlow > 0 ? '+' : '';
    summary.innerHTML += `<div class="defect-trend"><span class="wow-arrow ${trendClass}">${trendArrow} open WoW</span><br><span style="font-size:11px;color:var(--text-secondary)">net flow: ${netSign}${netFlow} (new ${defects.new} - closed ${defects.closed})</span></div>`;

    container.appendChild(summary);

    // Divider
    container.innerHTML += '<hr class="defect-divider">';

    // KPI cards (P1/P2/Unlabeled by Jira label)
    const kpiSection = document.createElement('div');
    kpiSection.innerHTML = '<div class="defect-section-label">By Label</div>';
    kpiSection.innerHTML += `
        <div class="kpi-row">
            <div class="kpi-card p1">
                <div class="kpi-value p1">${defects.p1}</div>
                <div class="kpi-label">P1</div>
                <div class="kpi-sub">Critical / Blocker</div>
            </div>
            <div class="kpi-card p2">
                <div class="kpi-value p2">${defects.p2}</div>
                <div class="kpi-label">P2</div>
                <div class="kpi-sub">High Priority</div>
            </div>
            <div class="kpi-card unlabeled">
                <div class="kpi-value unlabeled">${defects.other}</div>
                <div class="kpi-label">Unlabeled</div>
                <div class="kpi-sub">Needs triage</div>
            </div>
        </div>
    `;
    container.appendChild(kpiSection);

    // Divider + Mini P1/P2 trend chart
    container.innerHTML += '<hr class="defect-divider">';

    if (history && history.length > 0) {
        const last6 = history.slice(-6);
        const maxH = Math.max(...last6.map(w => (w.p1 || 0) + (w.p2 || 0)), 1);

        const trendSection = document.createElement('div');
        trendSection.innerHTML = '<div class="defect-section-label">P1 + P2 Trend</div>';

        const chart = document.createElement('div');
        chart.className = 'defect-chart mini-chart';

        const currentWs = metricsData ? metricsData.week_start : '';
        for (const w of last6) {
            const p1v = w.p1 || 0;
            const p2v = w.p2 || 0;
            const total = p1v + p2v;
            const col = document.createElement('div');
            col.className = 'defect-col' + (w.week_start === currentWs ? ' current' : '');

            const p1h = total > 0 ? (p1v / maxH * 100) : 0;
            const p2h = total > 0 ? (p2v / maxH * 100) : 0;

            const p1Label = p1h > 15 ? `<span class="seg-label">${p1v}</span>` : '';
            const p2Label = p2h > 15 ? `<span class="seg-label">${p2v}</span>` : '';

            col.innerHTML = `
                <span class="defect-col-total">${total}</span>
                <div class="defect-seg p2" style="height:${p2h}%">${p2Label}</div>
                <div class="defect-seg p1" style="height:${p1h}%">${p1Label}</div>
                <span class="defect-col-label">${w.week_start.slice(5)}</span>
            `;
            chart.appendChild(col);
        }
        trendSection.appendChild(chart);

        // Legend
        const legend = document.createElement('div');
        legend.className = 'stacked-legend';
        legend.innerHTML = `
            <span class="legend-item"><span class="legend-dot" style="background:var(--red)"></span>P1</span>
            <span class="legend-item"><span class="legend-dot" style="background:var(--amber)"></span>P2</span>
        `;
        trendSection.appendChild(legend);
        container.appendChild(trendSection);
    }
}

function renderDefectPriorityCard(containerId, defects, priorityHistory) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';

    container.innerHTML = '<div class="defect-section-label">All Open Defects by Jira Priority</div>';

    if (!priorityHistory || priorityHistory.length === 0) {
        container.innerHTML += '<div style="color:var(--text-secondary);font-size:13px">No priority data yet. Run a metrics collection to populate.</div>';
        return;
    }

    const last6 = priorityHistory.slice(-6);
    const levels = ['highest', 'high', 'medium', 'low', 'lowest'];
    const maxH = Math.max(...last6.map(w => levels.reduce((s, l) => s + (w[l] || 0), 0)), 1);

    const chart = document.createElement('div');
    chart.className = 'defect-chart';
    chart.style.height = '200px';
    chart.style.marginBottom = '20px';

    const currentWs = metricsData ? metricsData.week_start : '';
    for (const w of last6) {
        const total = levels.reduce((s, l) => s + (w[l] || 0), 0);
        const col = document.createElement('div');
        col.className = 'defect-col' + (w.week_start === currentWs ? ' current' : '');

        let colHtml = `<span class="defect-col-total">${total}</span>`;
        // Stack from bottom: lowest first, highest on top (column-reverse)
        for (const level of levels.slice().reverse()) {
            const v = w[level] || 0;
            const h = total > 0 ? (v / maxH * 100) : 0;
            const label = h > 12 ? `<span class="seg-label">${v}</span>` : '';
            colHtml += `<div class="defect-seg ${level}" style="height:${h}%">${label}</div>`;
        }
        colHtml += `<span class="defect-col-label">${w.week_start.slice(5)}</span>`;

        col.innerHTML = colHtml;
        chart.appendChild(col);
    }
    container.appendChild(chart);

    // Legend
    const legend = document.createElement('div');
    legend.className = 'stacked-legend';
    legend.innerHTML = `
        <span class="legend-item"><span class="legend-dot" style="background:#b71c1c"></span>Highest</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--red)"></span>High</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--amber)"></span>Medium</span>
        <span class="legend-item"><span class="legend-dot" style="background:#42a5f5"></span>Low</span>
        <span class="legend-item"><span class="legend-dot" style="background:#78909c"></span>Lowest</span>
        <span style="margin-left:auto;font-size:11px;color:var(--text-secondary)">4-wk avg net flow: ${defects.four_week_avg > 0 ? '+' : ''}${defects.four_week_avg}</span>
    `;
    container.appendChild(legend);
}

function renderEPSCard(containerId, devs) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!devs || devs.length === 0) { container.textContent = 'No data'; return; }

    const sorted = [...devs].sort((a, b) => (b.eps?.score || 0) - (a.eps?.score || 0));
    const maxScore = Math.max(...sorted.map(d => d.eps?.score || 0), 1);

    const list = document.createElement('div');
    list.className = 'eps-list';

    for (const d of sorted) {
        const score = d.eps?.score || 0;
        const label = (d.eps?.label || 'Emerging').toLowerCase();
        const pct = (score / maxScore * 100).toFixed(1);
        const row = document.createElement('div');
        row.className = 'eps-row';
        row.innerHTML = `
            <span class="eps-name">${escapeHtml(d.name)}</span>
            <div class="eps-bar-track"><div class="eps-bar-fill" style="width:${pct}%"></div></div>
            <span class="eps-score-val">${score}</span>
            <span class="eps-badge ${label}">${d.eps?.label || 'Emerging'}</span>
        `;
        list.appendChild(row);
    }
    container.appendChild(list);
}

function renderDetailTable(containerId, devs) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!devs || devs.length === 0) { container.textContent = 'No data'; return; }

    const table = document.createElement('table');
    table.className = 'metrics-table';
    table.innerHTML = `
        <thead><tr>
            <th>Developer</th>
            <th>Cycle Mean</th>
            <th>Cycle Med</th>
            <th>Cycle P85</th>
            <th>Lead Mean</th>
            <th>Lead Med</th>
            <th>Lead P85</th>
            <th>WoW</th>
        </tr></thead>
    `;

    const tbody = document.createElement('tbody');
    for (const d of devs) {
        const ct = d.cycle_time || {};
        const lt = d.lead_time || {};
        const wow = d.wow_cycle_time_delta;

        let wowHtml = '<span class="wow-arrow neutral">\u2014</span>';
        if (wow != null && Math.abs(wow) >= 0.05) {
            const arrow = wow < 0 ? '\u25BC' : '\u25B2';
            const cls = wow < 0 ? 'good' : 'bad';
            wowHtml = `<span class="wow-arrow ${cls}">${arrow} ${Math.abs(wow).toFixed(1)}</span>`;
        }

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${escapeHtml(d.name)}</td>
            <td>${ct.mean != null ? ct.mean + 'd' : '--'}</td>
            <td>${ct.median != null ? ct.median + 'd' : '--'}</td>
            <td>${ct.p85 != null ? ct.p85 + 'd' : '--'}</td>
            <td>${lt.mean != null ? lt.mean + 'd' : '--'}</td>
            <td>${lt.median != null ? lt.median + 'd' : '--'}</td>
            <td>${lt.p85 != null ? lt.p85 + 'd' : '--'}</td>
            <td>${wowHtml}</td>
        `;
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    container.appendChild(table);
}

// ---- Metrics Collection ----
async function triggerMetricsCollection() {
    const btn = document.getElementById('btn-collect');
    btn.classList.add('spinning');

    try {
        const resp = await fetch('/api/metrics/collect', { method: 'POST' });
        const data = await resp.json();
        const jobId = data.job_id;

        const poll = setInterval(async () => {
            try {
                const r = await fetch(`/api/metrics/collect/${jobId}`);
                const s = await r.json();
                if (s.status === 'done' || s.status === 'error') {
                    clearInterval(poll);
                    btn.classList.remove('spinning');
                    if (s.status === 'error') {
                        console.error('Metrics collection error:', s.error);
                    }
                    loadMetrics();
                }
            } catch {
                clearInterval(poll);
                btn.classList.remove('spinning');
            }
        }, 3000);
    } catch (e) {
        btn.classList.remove('spinning');
        console.error('Failed to trigger metrics collection:', e);
    }
}

// ---- Metrics Report ----
async function openMetricsReport() {
    const modal = document.getElementById('metrics-report-modal');
    const textEl = document.getElementById('metrics-report-text');
    textEl.textContent = 'Loading...';
    modal.classList.add('active');

    const url = currentWeekStart
        ? `/api/metrics/report?week_start=${currentWeekStart}`
        : '/api/metrics/report';
    try {
        const resp = await fetch(url);
        const data = await resp.json();
        textEl.textContent = data.text;
    } catch (e) {
        textEl.textContent = 'Failed to load report.';
        console.error('Failed to load metrics report:', e);
    }
}

// ---- Unestimated Tickets ----

async function showUnestimated() {
    const modal = document.getElementById('unestimated-modal');
    const body = document.getElementById('unestimated-body');
    body.innerHTML = '<div class="picker-loading">Loading tickets without story points...</div>';
    modal.classList.add('active');

    try {
        const resp = await fetch('/api/metrics/unestimated');
        const data = await resp.json();

        const jiraLink = document.getElementById('unestimated-jira-link');
        if (data.jira_url) {
            jiraLink.href = data.jira_url;
            jiraLink.style.display = '';
        } else {
            jiraLink.style.display = 'none';
        }

        if (!data.by_assignee || data.total === 0) {
            body.innerHTML = '<p style="color:var(--text-secondary)">All tickets have story points!</p>';
            return;
        }

        let html = `<p style="margin:0 0 12px;font-size:14px;color:var(--text-secondary)">${data.total} unestimated tickets</p>`;
        const jiraBase = data.jira_url ? data.jira_url.split('/issues/')[0] : '';
        for (const [assignee, tickets] of Object.entries(data.by_assignee)) {
            html += `<div class="unest-group">`;
            html += `<div class="unest-assignee">${escapeHtml(assignee)} <span class="unest-count">(${tickets.length})</span></div>`;
            for (const t of tickets) {
                const href = jiraBase ? `${jiraBase}/browse/${t.key}` : '#';
                html += `<a class="unest-ticket" href="${href}" target="_blank" rel="noopener">`;
                html += `<span class="unest-key">${escapeHtml(t.key)}</span>`;
                html += `<span class="unest-summary">${escapeHtml(t.summary)}</span>`;
                html += `<span class="unest-status">${escapeHtml(t.status)}</span>`;
                html += `</a>`;
            }
            html += `</div>`;
        }
        body.innerHTML = html;
    } catch (e) {
        body.innerHTML = '<p style="color:var(--red)">Failed to load unestimated tickets.</p>';
        console.error('Failed to load unestimated tickets:', e);
    }
}

function closeUnestimatedModal() {
    document.getElementById('unestimated-modal').classList.remove('active');
}

// ---- User Pickers (Jira / Bitbucket) ----
let bbUsersCache = null;
let slackUsersCache = null;
let jiraSearchTimer = null;

async function searchJiraUsers(query) {
    try {
        const resp = await fetch(`/api/metrics/jira-users?query=${encodeURIComponent(query)}`);
        return await resp.json();
    } catch { return []; }
}

async function fetchBBUsers() {
    if (bbUsersCache) return bbUsersCache;
    try {
        const resp = await fetch('/api/metrics/bitbucket-users');
        bbUsersCache = await resp.json();
        return bbUsersCache;
    } catch { return []; }
}

async function fetchSlackUsers() {
    if (slackUsersCache) return slackUsersCache;
    try {
        const resp = await fetch('/api/metrics/slack-users');
        slackUsersCache = await resp.json();
        return slackUsersCache;
    } catch { return []; }
}

function setupSlackSearchPicker(inputEl, dropdownEl, hiddenIdEl) {
    inputEl.addEventListener('input', async () => {
        const q = inputEl.value.trim().toLowerCase();
        if (q.length < 1) { dropdownEl.classList.remove('open'); return; }
        dropdownEl.innerHTML = '<div class="picker-loading">Searching...</div>';
        dropdownEl.classList.add('open');
        const users = await fetchSlackUsers();
        const items = users
            .filter(u => (u.real_name || '').toLowerCase().includes(q) || (u.display_name || '').toLowerCase().includes(q) || (u.email || '').toLowerCase().includes(q))
            .map(u => ({ label: u.real_name || u.display_name, sub: u.email || u.id, value: u.id }));
        renderPickerList(dropdownEl, items, inputEl, hiddenIdEl);
    });
}

function renderPickerList(dropdownEl, items, inputEl, hiddenIdEl, onSelect) {
    dropdownEl.innerHTML = '';
    if (items.length === 0) {
        dropdownEl.innerHTML = '<div class="picker-loading">No matches</div>';
        dropdownEl.classList.add('open');
        return;
    }
    for (const u of items) {
        const item = document.createElement('div');
        item.className = 'picker-item';
        item.innerHTML = `<span class="picker-main">${escapeHtml(u.label)}</span><span class="picker-sub">${escapeHtml(u.sub)}</span>`;
        item.addEventListener('mousedown', (e) => {
            e.preventDefault();
            e.stopPropagation();
            inputEl.value = u.label;
            if (hiddenIdEl) hiddenIdEl.value = u.value;
            // Delay closing so click doesn't bleed through to modal overlay
            setTimeout(() => dropdownEl.classList.remove('open'), 50);
            if (onSelect) onSelect(u);
        });
        dropdownEl.appendChild(item);
    }
    dropdownEl.classList.add('open');
}

function setupJiraSearchPicker(inputEl, dropdownEl, hiddenIdEl, onSelect) {
    inputEl.addEventListener('input', () => {
        const q = inputEl.value.trim();
        if (hiddenIdEl) hiddenIdEl.value = '';
        if (q.length < 2) { dropdownEl.classList.remove('open'); return; }
        dropdownEl.innerHTML = '<div class="picker-loading">Searching...</div>';
        dropdownEl.classList.add('open');
        clearTimeout(jiraSearchTimer);
        jiraSearchTimer = setTimeout(async () => {
            const users = await searchJiraUsers(q);
            const items = users.map(u => ({
                label: u.displayName,
                sub: u.emailAddress || u.accountId,
                value: u.accountId,
                email: u.emailAddress || '',
            }));
            renderPickerList(dropdownEl, items, inputEl, hiddenIdEl, onSelect);
        }, 300);
    });
}

function setupBBSearchPicker(inputEl, dropdownEl) {
    inputEl.addEventListener('input', async () => {
        const q = inputEl.value.trim().toLowerCase();
        if (q.length < 1) { dropdownEl.classList.remove('open'); return; }
        dropdownEl.innerHTML = '<div class="picker-loading">Searching...</div>';
        dropdownEl.classList.add('open');
        const users = await fetchBBUsers();
        const items = users
            .filter(u => u.display_name.toLowerCase().includes(q) || u.nickname.toLowerCase().includes(q))
            .map(u => ({ label: u.display_name, sub: u.nickname, value: u.nickname }));
        renderPickerList(dropdownEl, items, inputEl, null);
    });
}

function closeAllPickers() {
    document.querySelectorAll('.picker-dropdown').forEach(d => d.classList.remove('open'));
}

// ---- Merge Developers ----
let mergeSelected = new Set();

function updateMergeButton() {
    const btn = document.getElementById('btn-merge-devs');
    btn.style.display = mergeSelected.size >= 2 ? '' : 'none';
    btn.textContent = `Merge (${mergeSelected.size})`;
}

function toggleMergeSelect(devId) {
    if (mergeSelected.has(devId)) mergeSelected.delete(devId);
    else mergeSelected.add(devId);
    updateMergeButton();
    renderRoster();
}

async function mergeSelectedDevelopers() {
    if (mergeSelected.size < 2) return;
    const ids = [...mergeSelected];
    // First selected is the keeper
    const keepId = ids[0];
    const mergeIds = ids.slice(1);
    const keeperDev = developers.find(d => d.id === keepId);
    if (!confirm(`Merge ${mergeIds.length} developer(s) into "${keeperDev?.display_name || 'Unknown'}"? The first checked row becomes the keeper.`)) return;

    try {
        const resp = await fetch('/api/metrics/developers/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keep_id: keepId, merge_ids: mergeIds }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.detail || 'Merge failed');
            return;
        }
        mergeSelected.clear();
        updateMergeButton();
        await loadDevelopers();
        renderRoster();
    } catch (e) {
        console.error('Merge failed:', e);
    }
}

// ---- Developer Roster ----
function openRosterModal() {
    const modal = document.getElementById('roster-modal');
    modal.classList.add('active');
    mergeSelected.clear();
    updateMergeButton();
    renderRoster();
}

async function bulkAutomatchBB() {
    const btn = document.getElementById('btn-automatch-bb');
    btn.disabled = true;
    btn.textContent = 'Matching...';
    try {
        const resp = await fetch('/api/metrics/bitbucket-automatch', { method: 'POST' });
        const data = await resp.json();
        if (data.matched > 0) {
            await loadDevelopers();
            renderRoster();
            alert(`Matched ${data.matched} of ${data.total} developers:\n\n` +
                data.results.filter(r => r.bb_match).map(r => `${r.developer} → ${r.bb_match}`).join('\n'));
        } else {
            const unmatched = data.results.filter(r => !r.bb_match).map(r => r.developer);
            if (unmatched.length === 0) {
                alert('All developers already have Bitbucket usernames.');
            } else {
                alert(`No matches found for:\n${unmatched.join('\n')}`);
            }
        }
    } catch (e) {
        console.error('BB auto-match failed:', e);
        alert('Failed to auto-match Bitbucket users.');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Auto-match BB';
    }
}

async function bulkAutomatchSlack() {
    const btn = document.getElementById('btn-automatch-slack');
    btn.disabled = true;
    btn.textContent = 'Matching...';
    try {
        const resp = await fetch('/api/metrics/slack-automatch', { method: 'POST' });
        const data = await resp.json();
        if (data.matched > 0) {
            await loadDevelopers();
            renderRoster();
            alert(`Matched ${data.matched} of ${data.total} developers:\n\n` +
                data.results.filter(r => r.slack_match).map(r => `${r.developer} → ${r.slack_match}`).join('\n'));
        } else {
            const unmatched = data.results.filter(r => !r.slack_match).map(r => r.developer);
            if (unmatched.length === 0) {
                alert('All developers already have Slack user IDs.');
            } else {
                alert(`No matches found for:\n${unmatched.join('\n')}`);
            }
        }
    } catch (e) {
        console.error('Slack auto-match failed:', e);
        alert('Failed to auto-match Slack users.');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Auto-match Slack';
    }
}

async function sendSlackReminders() {
    const btn = document.getElementById('btn-slack-remind');
    if (!confirm('Send Slack DM reminders to developers with unestimated tickets?')) return;
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = 'Sending...';
    try {
        const resp = await fetch('/api/metrics/slack-remind', { method: 'POST' });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.detail || 'Failed to trigger reminders');
            return;
        }
        const { job_id } = await resp.json();

        // Poll for completion
        let status = 'pending';
        let result = null;
        while (status === 'pending' || status === 'running') {
            await new Promise(r => setTimeout(r, 1000));
            const pollResp = await fetch(`/api/metrics/slack-remind/${job_id}`);
            const pollData = await pollResp.json();
            status = pollData.status;
            result = pollData.result || null;
            if (pollData.error) {
                alert(`Error: ${pollData.error}`);
                return;
            }
        }

        if (result) {
            const msg = `Sent: ${result.sent} | Skipped (0 unestimated): ${result.skipped}` +
                (result.errors.length ? `\nErrors: ${result.errors.join(', ')}` : '');
            alert(msg);
        }
    } catch (e) {
        console.error('Slack remind failed:', e);
        alert('Failed to send Slack reminders.');
    } finally {
        btn.disabled = false;
        btn.textContent = origText;
    }
}

function renderRoster() {
    const list = document.getElementById('roster-list');
    list.innerHTML = '';

    if (developers.length === 0) {
        list.innerHTML = '<p style="color:var(--text-secondary);font-size:13px">No developers yet.</p>';
        return;
    }

    const keeperId = mergeSelected.size > 0 ? [...mergeSelected][0] : null;

    for (const d of developers) {
        const row = document.createElement('div');
        row.className = 'roster-row';

        // Merge checkbox
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'roster-checkbox';
        cb.checked = mergeSelected.has(d.id);
        cb.addEventListener('change', () => toggleMergeSelect(d.id));
        row.appendChild(cb);

        const nameSpan = document.createElement('span');
        nameSpan.className = 'roster-name';
        nameSpan.textContent = d.display_name;
        row.appendChild(nameSpan);

        // Keeper label
        if (d.id === keeperId && mergeSelected.size >= 2) {
            const kl = document.createElement('span');
            kl.className = 'merge-keeper-label';
            kl.textContent = 'keeper';
            row.appendChild(kl);
        }

        const emailSpan = document.createElement('span');
        emailSpan.className = 'roster-email';
        emailSpan.textContent = d.email;
        row.appendChild(emailSpan);

        const idsSpan = document.createElement('span');
        idsSpan.className = 'roster-ids';
        idsSpan.textContent = [d.jira_account_id ? 'Jira' : '', d.bitbucket_username ? 'BB' : '', d.slack_user_id ? 'Slack' : ''].filter(Boolean).join(' ');
        row.appendChild(idsSpan);

        const editBtn = document.createElement('button');
        editBtn.className = 'btn-action';
        editBtn.textContent = '\u270E';
        editBtn.title = 'Edit';
        editBtn.style.opacity = '1';
        editBtn.addEventListener('click', () => openDevEditModal(d));
        row.appendChild(editBtn);

        const del = document.createElement('button');
        del.className = 'btn-delete';
        del.textContent = '\u00d7';
        del.style.opacity = '1';
        del.addEventListener('click', () => removeDeveloper(d.id));
        row.appendChild(del);
        list.appendChild(row);
    }
}

function openDevEditModal(dev) {
    const isNew = !dev;
    document.getElementById('dev-edit-id').value = dev ? dev.id : '';
    document.getElementById('dev-edit-name').value = dev ? dev.display_name : '';
    document.getElementById('dev-edit-email').value = dev ? dev.email : '';
    document.getElementById('dev-edit-jira').value = dev ? dev.jira_account_id || '' : '';
    document.getElementById('dev-edit-jira-id').value = dev ? dev.jira_account_id || '' : '';
    document.getElementById('dev-edit-bb').value = dev ? dev.bitbucket_username || '' : '';
    document.getElementById('dev-edit-slack').value = dev ? dev.slack_user_id || '' : '';
    document.getElementById('dev-edit-slack-id').value = dev ? dev.slack_user_id || '' : '';
    document.getElementById('dev-edit-team').value = dev ? dev.team || 'engineering' : 'engineering';
    document.getElementById('dev-edit-role').value = dev ? dev.role || 'Engineer' : 'Engineer';
    document.querySelector('#dev-edit-modal .modal-title').textContent = isNew ? 'Add Developer' : 'Edit Developer';
    document.getElementById('btn-save-dev').textContent = isNew ? 'Add' : 'Save';
    document.getElementById('dev-edit-modal').classList.add('active');
    document.getElementById('dev-edit-jira').focus();
}

async function saveDevEdit() {
    const id = document.getElementById('dev-edit-id').value;
    const jiraHidden = document.getElementById('dev-edit-jira-id').value.trim();
    const jiraVisible = document.getElementById('dev-edit-jira').value.trim();
    const slackHidden = document.getElementById('dev-edit-slack-id').value.trim();
    const slackVisible = document.getElementById('dev-edit-slack').value.trim();
    const payload = {
        display_name: document.getElementById('dev-edit-name').value.trim(),
        email: document.getElementById('dev-edit-email').value.trim(),
        jira_account_id: jiraHidden || jiraVisible || null,
        bitbucket_username: document.getElementById('dev-edit-bb').value.trim() || null,
        slack_user_id: slackHidden || slackVisible || null,
        team: document.getElementById('dev-edit-team').value.trim() || 'engineering',
        role: document.getElementById('dev-edit-role').value.trim() || 'Engineer',
    };
    if (!payload.display_name || !payload.email) return;

    try {
        if (id) {
            // Update existing
            await fetch(`/api/metrics/developers/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } else {
            // Create new
            const resp = await fetch('/api/metrics/developers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (resp.status === 409) {
                alert('A developer with this email already exists.');
                return;
            }
        }
        document.getElementById('dev-edit-modal').classList.remove('active');
        await loadDevelopers();
        renderRoster();
    } catch (e) {
        console.error('Failed to save developer:', e);
    }
}


async function removeDeveloper(devId) {
    try {
        await fetch(`/api/metrics/developers/${devId}`, { method: 'DELETE' });
        await loadDevelopers();
        renderRoster();
    } catch (e) {
        console.error('Failed to remove developer:', e);
    }
}

// ---- Status Board ----

function formatDuration(seconds) {
    if (seconds == null || seconds < 0) return '--';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    return `${days}d ${hours}h`;
}

function durationColorClass(seconds) {
    if (seconds == null) return '';
    const days = seconds / 86400;
    if (days < 3) return 'sb-green';
    if (days < 7) return 'sb-amber';
    return 'sb-red';
}

async function loadStatusBoard() {
    const loading = document.getElementById('sb-loading');
    const error = document.getElementById('sb-error');
    const empty = document.getElementById('sb-empty');
    const cards = document.getElementById('sb-summary-cards');
    const groups = document.getElementById('sb-groups');
    const pagination = document.getElementById('sb-pagination');

    loading.style.display = '';
    error.style.display = 'none';
    empty.style.display = 'none';
    cards.innerHTML = '';
    groups.innerHTML = '';
    pagination.innerHTML = '';

    const params = new URLSearchParams();
    const project = document.getElementById('sb-filter-project').value;
    const priority = document.getElementById('sb-filter-priority').value;
    const assignee = document.getElementById('sb-filter-assignee').value;
    const search = document.getElementById('sb-search').value.trim();
    if (project) params.set('project', project);
    if (priority) params.set('priority', priority);
    if (assignee) params.set('assignee', assignee);
    if (search) params.set('search', search);
    params.set('page', sbPage);
    params.set('sort_by', sbSortBy);
    params.set('sort_dir', sbSortDir);

    try {
        const resp = await fetch(`/api/status-board/dashboard?${params}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        sbData = await resp.json();
        loading.style.display = 'none';

        if (sbData.total_tickets === 0) {
            empty.style.display = '';
            return;
        }

        renderSBCards(sbData.cards);
        renderSBGroups(sbData.groups, sbData.statuses);
        renderSBPagination(sbData.total_tickets, sbData.page, sbData.page_size);
        populateSBFilters(sbData);

        // Update last synced
        const syncEl = document.getElementById('sb-last-synced');
        if (sbData.last_synced) {
            const ago = timeAgo(new Date(sbData.last_synced));
            syncEl.textContent = `Last synced: ${ago}`;
            // Warning if > 1 hour
            const ms = Date.now() - new Date(sbData.last_synced).getTime();
            syncEl.style.color = ms > 3600000 ? 'var(--amber)' : '';
        } else {
            syncEl.textContent = 'Never synced';
            syncEl.style.color = 'var(--amber)';
        }
    } catch (e) {
        loading.style.display = 'none';
        error.style.display = '';
        document.getElementById('sb-error-msg').textContent = e.message;
    }
}

function timeAgo(date) {
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

function renderSBCards(cards) {
    const container = document.getElementById('sb-summary-cards');
    container.innerHTML = cards.map(c => `
        <div class="sb-card">
            <div class="sb-card-status">${escapeHtml(c.status)}</div>
            <div class="sb-card-row">
                <span class="sb-card-label">Open avg</span>
                <span class="sb-card-value ${durationColorClass(c.open_avg_seconds)}">${formatDuration(c.open_avg_seconds)}</span>
            </div>
            <div class="sb-card-row">
                <span class="sb-card-label">Closed avg</span>
                <span class="sb-card-value sb-muted">${formatDuration(c.closed_avg_seconds)}</span>
            </div>
            <div class="sb-card-sublabel">4-week rolling</div>
        </div>
    `).join('');
}

function renderSBGroups(groups, statuses) {
    const container = document.getElementById('sb-groups');
    container.innerHTML = groups.map(g => {
        const label = g.assignee_type === 'non_roster' ? '<span class="sb-assignee-label">Assigned but not in roster</span>' :
                      g.assignee_type === 'unassigned' ? '<span class="sb-assignee-label">No Jira assignee</span>' : '';
        return `
        <div class="sb-group">
            <div class="sb-group-header" onclick="toggleSBGroup(this)">
                <span class="sb-chevron">&#9660;</span>
                <span class="sb-group-name">${escapeHtml(g.assignee_name)}</span>
                <span class="sb-group-count">${g.ticket_count} ticket${g.ticket_count !== 1 ? 's' : ''}</span>
                ${label}
            </div>
            <table class="sb-table">
                <thead><tr>
                    <th style="width:90px" onclick="sbSort('issue_key')">Key</th>
                    <th onclick="sbSort('summary')">Summary</th>
                    <th style="width:55px" onclick="sbSort('priority')">Pri</th>
                    <th style="width:60px" onclick="sbSort('project_key')">Proj</th>
                    <th style="width:90px" onclick="sbSort('current_status')">Status</th>
                    ${statuses.map(s => `<th class="sb-time-header" onclick="sbSort('status_time_${escapeHtml(s)}')">${escapeHtml(s)}</th>`).join('')}
                </tr></thead>
                <tbody>
                    ${g.tickets.map(t => renderSBTicketRow(t, statuses)).join('')}
                </tbody>
            </table>
        </div>`;
    }).join('');
}

function renderSBTicketRow(t, statuses) {
    const jiraUrl = t.jira_url || '#';
    const priBadge = priorityToBadge(t.priority);
    return `
        <tr class="sb-ticket-row" data-key="${escapeHtml(t.issue_key)}">
            <td><a class="sb-ticket-key" href="${jiraUrl}" target="_blank" onclick="event.stopPropagation()">${escapeHtml(t.issue_key)}</a></td>
            <td class="sb-ticket-summary" onclick="toggleSBDetail('${escapeHtml(t.issue_key)}')">${escapeHtml(t.summary)}</td>
            <td>${priBadge}</td>
            <td><span class="sb-badge-project">${escapeHtml(t.project_key)}</span></td>
            <td><span class="sb-badge-status">${escapeHtml(t.current_status)}</span></td>
            ${statuses.map(s => {
                const sec = t.status_times[s];
                const isCurrent = s === t.current_status;
                if (sec == null && !isCurrent) return '<td class="sb-time-cell sb-empty-cell">--</td>';
                const val = isCurrent ? t.current_status_seconds : sec;
                const color = durationColorClass(val);
                const currentCls = isCurrent ? ' sb-current' : '';
                return `<td class="sb-time-cell ${color}${currentCls}">${formatDuration(val)}</td>`;
            }).join('')}
        </tr>
        <tr class="sb-detail-row" id="sb-detail-${t.issue_key.replace(/[^a-zA-Z0-9-]/g, '_')}" style="display:none">
            <td colspan="${5 + statuses.length}">
                <div class="sb-detail-inner" id="sb-detail-inner-${t.issue_key.replace(/[^a-zA-Z0-9-]/g, '_')}">
                    <em>Loading transitions...</em>
                </div>
            </td>
        </tr>`;
}

function priorityToBadge(priority) {
    if (!priority) return '';
    const p = priority.toLowerCase();
    let cls = 'sb-badge-pri';
    if (p === 'highest' || p === 'p1') cls += ' badge-p1';
    else if (p === 'high' || p === 'p2') cls += ' badge-p2';
    else if (p === 'medium' || p === 'p3') cls += ' badge-p3';
    else cls += ' badge-p4';
    return `<span class="${cls}">${escapeHtml(priority)}</span>`;
}

function toggleSBGroup(header) {
    const table = header.nextElementSibling;
    const chevron = header.querySelector('.sb-chevron');
    if (table.style.display === 'none') {
        table.style.display = '';
        chevron.classList.remove('sb-collapsed');
    } else {
        table.style.display = 'none';
        chevron.classList.add('sb-collapsed');
    }
}

async function toggleSBDetail(issueKey) {
    const safeKey = issueKey.replace(/[^a-zA-Z0-9-]/g, '_');
    const row = document.getElementById(`sb-detail-${safeKey}`);
    const inner = document.getElementById(`sb-detail-inner-${safeKey}`);
    if (!row) return;

    if (row.style.display === 'none') {
        row.style.display = '';
        // Mark the ticket row as expanded
        row.previousElementSibling.classList.add('sb-expanded');
        // Fetch transitions
        try {
            const resp = await fetch(`/api/status-board/ticket/${encodeURIComponent(issueKey)}/transitions`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            inner.innerHTML = renderTransitionTable(data.transitions, issueKey);
        } catch (e) {
            inner.innerHTML = `<span style="color:var(--red)">Failed to load: ${escapeHtml(e.message)}</span>`;
        }
    } else {
        row.style.display = 'none';
        row.previousElementSibling.classList.remove('sb-expanded');
    }
}

function renderTransitionTable(transitions, issueKey) {
    if (!transitions || transitions.length === 0) {
        return '<em>No status transitions recorded</em>';
    }
    const jiraUrl = `https://ninjio.atlassian.net/browse/${issueKey}`;
    let rows = transitions.map((t, i) => {
        const isCurrent = i === transitions.length - 1 && !t.exited_at;
        const marker = isCurrent ? '<span class="sb-current-marker"></span>' : '';
        const durColor = isCurrent ? durationColorClass(t.duration_seconds) : '';
        return `<tr>
            <td>${marker}${escapeHtml(t.status)}</td>
            <td>${escapeHtml(t.entered_at || '--')}</td>
            <td>${t.exited_at ? escapeHtml(t.exited_at) : '<em>now</em>'}</td>
            <td class="${durColor}">${formatDuration(t.duration_seconds)}</td>
        </tr>`;
    }).join('');
    return `
        <h4>Status Transition History</h4>
        <table class="sb-transition-table">
            <thead><tr><th>Status</th><th>Entered</th><th>Exited</th><th>Duration</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <div class="sb-detail-footer">
            <a href="${jiraUrl}" target="_blank">Open in Jira &#8599;</a>
        </div>`;
}

function renderSBPagination(total, page, pageSize) {
    const container = document.getElementById('sb-pagination');
    const totalPages = Math.ceil(total / pageSize);
    if (totalPages <= 1) { container.innerHTML = ''; return; }
    container.innerHTML = `
        <button ${page <= 1 ? 'disabled' : ''} onclick="sbGoPage(${page - 1})">&laquo; Prev</button>
        <span class="sb-page-info">Page ${page} of ${totalPages} (${total} tickets)</span>
        <button ${page >= totalPages ? 'disabled' : ''} onclick="sbGoPage(${page + 1})">Next &raquo;</button>`;
}

function sbGoPage(p) {
    sbPage = p;
    loadStatusBoard();
}

function sbSort(field) {
    if (sbSortBy === field) {
        sbSortDir = sbSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        sbSortBy = field;
        sbSortDir = 'desc';
    }
    loadStatusBoard();
}

function populateSBFilters(data) {
    // Populate project filter
    const projSelect = document.getElementById('sb-filter-project');
    if (projSelect.options.length <= 1) {
        const projects = [...new Set(data.groups.flatMap(g => g.tickets.map(t => t.project_key)))].sort();
        projects.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p;
            opt.textContent = p;
            projSelect.appendChild(opt);
        });
    }
    // Populate assignee filter
    const assSelect = document.getElementById('sb-filter-assignee');
    if (assSelect.options.length <= 1) {
        const assignees = data.groups
            .filter(g => g.assignee_type === 'roster')
            .map(g => g.assignee_name)
            .sort();
        assignees.forEach(a => {
            const opt = document.createElement('option');
            opt.value = a;
            opt.textContent = a;
            assSelect.appendChild(opt);
        });
    }
}

async function refreshStatusBoard() {
    const btn = document.getElementById('btn-sb-refresh');
    btn.disabled = true;
    btn.textContent = 'Syncing...';
    try {
        const resp = await fetch('/api/status-board/refresh', { method: 'POST' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        // Wait a bit for sync to start, then poll
        setTimeout(async () => {
            await loadStatusBoard();
            btn.disabled = false;
            btn.textContent = 'Refresh';
        }, 3000);
    } catch (e) {
        btn.disabled = false;
        btn.textContent = 'Refresh';
        alert('Refresh failed: ' + e.message);
    }
}
