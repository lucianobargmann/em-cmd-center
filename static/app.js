/* EM Command Center — UI */

let currentFilter = '';
let tasks = [];
let goals = [];
let completedCollapsed = false;

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    setHeaderDate();
    loadTasks();
    loadGoals();
    loadAgentStatus();
    setupListeners();
    // Poll agent status every 60s
    setInterval(loadAgentStatus, 60000);
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
    document.getElementById('btn-add-goal').addEventListener('click', () => openGoalModal());
    document.getElementById('goal-modal-close').addEventListener('click', () => {
        document.getElementById('goal-modal').classList.remove('active');
    });
    document.getElementById('goal-modal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) e.currentTarget.classList.remove('active');
    });
    document.getElementById('btn-save-goal').addEventListener('click', saveGoal);
}

// ---- API calls ----
async function loadTasks() {
    const url = currentFilter ? `/api/tasks?filter=${currentFilter}` : '/api/tasks';
    try {
        const resp = await fetch(url);
        tasks = await resp.json();
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

    if (goals.length > 0) {
        const ws = goals[0].week_start;
        if (ws) {
            const d = new Date(ws + 'T00:00:00');
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            label.textContent = `CTO Goals (Week of ${months[d.getMonth()]} ${d.getDate()})`;
        }
    } else {
        label.textContent = 'CTO Goals';
    }

    for (const g of goals) {
        if (g.status === 'archived') continue;
        const row = document.createElement('div');
        row.className = 'goal-row';

        // Status icon (clickable to cycle)
        const icon = document.createElement('span');
        icon.className = 'goal-status-icon';
        icon.textContent = g.status === 'completed' ? '\u2705' : '\uD83D\uDD35';
        icon.title = `Status: ${g.status} (click to cycle)`;
        icon.addEventListener('click', () => cycleGoalStatus(g));
        row.appendChild(icon);

        // Title
        const title = document.createElement('span');
        title.className = 'goal-title' + (g.status === 'completed' ? ' done' : '');
        title.textContent = g.title;
        title.addEventListener('click', () => openGoalModal(g));
        row.appendChild(title);

        // Progress notes (inline editable)
        const notes = document.createElement('input');
        notes.className = 'goal-notes-inline';
        notes.type = 'text';
        notes.value = g.progress_notes || '';
        notes.placeholder = 'progress...';
        notes.addEventListener('blur', () => updateGoalNotes(g.id, notes.value));
        notes.addEventListener('keydown', (e) => { if (e.key === 'Enter') notes.blur(); });
        row.appendChild(notes);

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

async function updateGoalNotes(goalId, notes) {
    try {
        await fetch(`/api/goals/${goalId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ progress_notes: notes }),
        });
    } catch (e) {
        console.error('Failed to update goal notes:', e);
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

function openGoalModal(goal) {
    const modal = document.getElementById('goal-modal');
    document.getElementById('goal-modal-title').textContent = goal ? 'Edit Goal' : 'Add Goal';
    document.getElementById('goal-edit-id').value = goal ? goal.id : '';
    document.getElementById('goal-title-input').value = goal ? goal.title : '';
    document.getElementById('goal-desc-input').value = goal ? (goal.description || '') : '';
    document.getElementById('goal-notes-input').value = goal ? (goal.progress_notes || '') : '';
    modal.classList.add('active');
    document.getElementById('goal-title-input').focus();
}

async function saveGoal() {
    const id = document.getElementById('goal-edit-id').value;
    const title = document.getElementById('goal-title-input').value.trim();
    if (!title) return;

    const payload = {
        title,
        description: document.getElementById('goal-desc-input').value.trim() || null,
        progress_notes: document.getElementById('goal-notes-input').value.trim() || null,
    };

    try {
        if (id) {
            await fetch(`/api/goals/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } else {
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
    p1: 'P1 — URGENT · DO NOW',
    p2: 'P2 — THIS WEEK',
    p3: 'P3 — WHEN POSSIBLE',
    p4: 'P4 — BACKLOG',
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

    // Delete button
    const del = document.createElement('button');
    del.className = 'btn-delete';
    del.textContent = '×';
    del.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteTask(task.id);
    });
    row.appendChild(del);

    return row;
}
