/* ══════════════════════════════════════════════════
   Drexel Autopilot — Frontend Logic
   ══════════════════════════════════════════════════ */

const API = '';
let state = {
  courses: [],
  assignments: [],
  grades: [],
  user: null,
  currentFilter: 'upcoming',
};

// ── Helpers ──────────────────────────────────────

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

async function api(path) {
  try {
    const res = await fetch(`${API}${path}`);
    return await res.json();
  } catch (e) {
    console.error(`API error: ${path}`, e);
    return null;
  }
}

function timeGreeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatDateLong(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function daysUntil(iso) {
  if (!iso) return Infinity;
  const now = new Date();
  const due = new Date(iso);
  return Math.ceil((due - now) / (1000 * 60 * 60 * 24));
}

function urgencyClass(iso, submitted) {
  if (submitted) return 'done';
  const days = daysUntil(iso);
  if (days <= 1) return 'urgent';
  if (days <= 3) return 'soon';
  return 'later';
}

function gradeClass(score) {
  if (score >= 90) return 'a';
  if (score >= 80) return 'b';
  if (score >= 70) return 'c';
  return 'd';
}

function gradeToGPA(letter) {
  const map = { 'A': 4.0, 'A-': 3.7, 'B+': 3.3, 'B': 3.0, 'B-': 2.7, 'C+': 2.3, 'C': 2.0, 'C-': 1.7, 'D+': 1.3, 'D': 1.0, 'F': 0 };
  return map[letter] ?? null;
}

const COURSE_COLORS = [
  '#07294D', '#1a6fc4', '#2d8659', '#9b59b6',
  '#e67e22', '#e74c3c', '#1abc9c', '#34495e',
];

function courseColor(index) {
  return COURSE_COLORS[index % COURSE_COLORS.length];
}

function stripHTML(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  return tmp.textContent || tmp.innerText || '';
}

// ── Navigation ───────────────────────────────────

function switchView(name) {
  $$('.view').forEach(v => v.classList.remove('active'));
  $$('.nav-link').forEach(l => l.classList.remove('active'));

  const view = $(`#view-${name}`);
  if (view) {
    view.classList.remove('active');
    // Force reflow for animation
    void view.offsetWidth;
    view.classList.add('active');
  }

  const link = $(`.nav-link[data-view="${name}"]`);
  if (link) link.classList.add('active');

  if (name === 'assignments') renderAssignments();
  if (name === 'grades') renderGrades();
}

document.addEventListener('click', (e) => {
  const viewBtn = e.target.closest('[data-view]');
  if (viewBtn) {
    e.preventDefault();
    switchView(viewBtn.dataset.view);
  }

  const filterBtn = e.target.closest('[data-filter]');
  if (filterBtn) {
    $$('.pill').forEach(p => p.classList.remove('active'));
    filterBtn.classList.add('active');
    state.currentFilter = filterBtn.dataset.filter;
    renderAssignments();
  }

  const courseCard = e.target.closest('.course-card');
  if (courseCard) {
    const id = courseCard.dataset.courseId;
    if (id) loadCourseDetail(Number(id));
  }
});

// ── Render: Dashboard ────────────────────────────

function renderDashboard() {
  // Greeting
  const name = state.user?.name?.split(' ')[0] || '';
  $('#greeting').textContent = `${timeGreeting()}${name ? ', ' + name : ''}.`;

  // Stats
  $('#stat-courses').textContent = state.courses.length;

  const now = new Date();
  const weekEnd = new Date(now);
  weekEnd.setDate(weekEnd.getDate() + 7);
  const dueThisWeek = state.assignments.filter(a =>
    !a.submitted && a.due && new Date(a.due) <= weekEnd && new Date(a.due) >= now
  ).length;
  $('#stat-due-week').textContent = dueThisWeek;

  // GPA estimate
  const gpas = state.grades
    .map(g => gradeToGPA(g.letter))
    .filter(g => g !== null);
  if (gpas.length > 0) {
    const avg = gpas.reduce((a, b) => a + b, 0) / gpas.length;
    $('#stat-gpa').textContent = avg.toFixed(2);
  } else {
    $('#stat-gpa').textContent = '—';
  }

  // Up Next (top 5 upcoming)
  const upcoming = state.assignments
    .filter(a => !a.submitted && a.due && new Date(a.due) >= now)
    .sort((a, b) => new Date(a.due) - new Date(b.due))
    .slice(0, 5);

  const upNextEl = $('#up-next-list');
  if (upcoming.length === 0) {
    upNextEl.innerHTML = `
      <div class="empty-state">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
        <p>You're all caught up. Nothing due soon.</p>
      </div>`;
  } else {
    upNextEl.innerHTML = upcoming.map(a => assignmentCard(a)).join('');
  }

  // Course grid
  const courseGrid = $('#course-grid');
  courseGrid.innerHTML = state.courses.map((c, i) => `
    <div class="course-card" data-course-id="${c.id}" style="--card-accent: ${courseColor(i)}">
      <div class="course-code">${esc(c.code)}</div>
      <div class="course-name">${esc(c.name)}</div>
      ${c.term ? `<div class="course-term">${esc(c.term)}</div>` : ''}
      ${c.grade?.letter || c.grade?.score ? `
        <div class="course-grade">
          ${c.grade.letter ? `<span class="course-grade-letter">${esc(c.grade.letter)}</span>` : ''}
          ${c.grade.score != null ? `<span class="course-grade-score">${c.grade.score}%</span>` : ''}
        </div>
      ` : ''}
    </div>
  `).join('');
}

function assignmentCard(a) {
  const d = new Date(a.due);
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const urg = urgencyClass(a.due, a.submitted);
  const days = daysUntil(a.due);
  let timeLabel = '';
  if (a.submitted) timeLabel = 'Done';
  else if (days <= 0) timeLabel = 'Today';
  else if (days === 1) timeLabel = 'Tomorrow';
  else timeLabel = `${days}d left`;

  return `
    <a class="assignment-card ${a.submitted ? 'submitted' : ''}" ${a.url ? `href="${a.url}" target="_blank"` : ''}>
      <div class="due-badge ${urg}">
        <span class="due-month">${months[d.getMonth()]}</span>
        <span class="due-day">${d.getDate()}</span>
      </div>
      <div class="assignment-info">
        <div class="assignment-name">${esc(a.name)}</div>
        <div class="assignment-meta">
          <span>${esc(a.course || a.course_code || '')}</span>
          <span>${timeLabel}</span>
        </div>
      </div>
      ${a.points ? `<span class="assignment-points">${a.points} pts</span>` : ''}
      <div class="assignment-check ${a.submitted ? 'done' : ''}">
        ${a.submitted ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>' : ''}
      </div>
    </a>`;
}

// ── Render: Assignments ──────────────────────────

function renderAssignments() {
  const list = $('#assignments-list');
  const now = new Date();
  let filtered;

  if (state.currentFilter === 'upcoming') {
    filtered = state.assignments
      .filter(a => !a.submitted && a.due && new Date(a.due) >= now)
      .sort((a, b) => new Date(a.due) - new Date(b.due));
  } else if (state.currentFilter === 'submitted') {
    filtered = state.assignments
      .filter(a => a.submitted)
      .sort((a, b) => new Date(b.due) - new Date(a.due));
  } else {
    filtered = [...state.assignments].sort((a, b) => {
      if (!a.due) return 1;
      if (!b.due) return -1;
      return new Date(a.due) - new Date(b.due);
    });
  }

  if (filtered.length === 0) {
    list.innerHTML = `
      <div class="empty-state">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>
        <p>No assignments to show.</p>
      </div>`;
    return;
  }

  // Group by course
  const groups = {};
  filtered.forEach(a => {
    const key = a.course || 'Other';
    if (!groups[key]) groups[key] = [];
    groups[key].push(a);
  });

  let html = '';
  for (const [course, items] of Object.entries(groups)) {
    html += `<div class="section"><div class="section-header"><h2>${esc(course)}</h2></div><div class="card-list">`;
    html += items.map(a => assignmentCard(a)).join('');
    html += `</div></div>`;
  }

  list.innerHTML = html;
}

// ── Render: Grades ───────────────────────────────

function renderGrades() {
  const grid = $('#grades-grid');

  if (state.grades.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        <p>No grades available yet.</p>
      </div>`;
    return;
  }

  grid.innerHTML = state.grades.map(g => {
    const score = g.score ?? 0;
    const gc = gradeClass(score);
    return `
      <div class="grade-row">
        <div class="grade-course">
          <div class="grade-course-name">${esc(g.course)}</div>
          <div class="grade-course-code">${esc(g.code)}</div>
        </div>
        <div class="grade-display">
          <div class="grade-bar-container">
            <div class="grade-bar ${gc}" style="width: 0%;" data-target="${score}"></div>
          </div>
          <span class="grade-score">${score != null ? score + '%' : ''}</span>
          <span class="grade-letter">${esc(g.letter || '—')}</span>
        </div>
      </div>`;
  }).join('');

  // Animate bars
  requestAnimationFrame(() => {
    $$('.grade-bar').forEach(bar => {
      const target = bar.dataset.target;
      bar.style.width = `${target}%`;
    });
  });
}

// ── Render: Course Detail ────────────────────────

async function loadCourseDetail(courseId) {
  switchView('course');
  const detail = $('#course-detail');
  detail.innerHTML = '<div class="skeleton-card" style="height:200px;margin-bottom:16px"></div><div class="skeleton-card" style="height:300px"></div>';

  const data = await api(`/api/courses/${courseId}`);
  if (!data || data.error) {
    detail.innerHTML = '<div class="empty-state"><p>Could not load course details.</p></div>';
    return;
  }

  const now = new Date();
  const upcoming = (data.assignments || [])
    .filter(a => !a.submitted && a.due && new Date(a.due) >= now)
    .sort((a, b) => new Date(a.due) - new Date(b.due));
  const past = (data.assignments || [])
    .filter(a => a.submitted || !a.due || new Date(a.due) < now)
    .sort((a, b) => new Date(b.due || 0) - new Date(a.due || 0));

  detail.innerHTML = `
    <div class="detail-header">
      <h1>${esc(data.name)}</h1>
      <div class="detail-meta">${esc(data.code)}${data.term ? ' · ' + esc(data.term) : ''}</div>
      ${data.grade?.letter || data.grade?.score != null ? `
        <div class="detail-grade-big">
          ${data.grade.letter ? `<span class="letter">${esc(data.grade.letter)}</span>` : ''}
          ${data.grade.score != null ? `<span class="score">${data.grade.score}%</span>` : ''}
        </div>
      ` : ''}
    </div>

    ${data.announcements?.length ? `
      <div class="detail-section">
        <h2>Recent Announcements</h2>
        ${data.announcements.map(a => `
          <div class="announcement-card">
            <div class="announcement-title">${esc(a.title)}</div>
            <div class="announcement-date">${formatDateLong(a.date)}</div>
            ${a.message ? `<div class="announcement-body">${esc(stripHTML(a.message)).slice(0, 200)}</div>` : ''}
          </div>
        `).join('')}
      </div>
    ` : ''}

    ${upcoming.length ? `
      <div class="detail-section">
        <h2>Upcoming (${upcoming.length})</h2>
        <div class="card-list">
          ${upcoming.map(a => assignmentCard({
            ...a,
            course: data.code,
          })).join('')}
        </div>
      </div>
    ` : ''}

    ${past.length ? `
      <div class="detail-section">
        <h2>Past & Submitted (${past.length})</h2>
        <div class="card-list">
          ${past.slice(0, 20).map(a => assignmentCard({
            ...a,
            course: data.code,
          })).join('')}
        </div>
      </div>
    ` : ''}

    ${data.modules?.length ? `
      <div class="detail-section">
        <h2>Modules</h2>
        ${data.modules.map(m => `
          <div class="module-card">
            <div class="module-name">${esc(m.name)}</div>
            <div class="module-items">
              ${(m.items || []).slice(0, 8).map(i => `
                <div class="module-item">
                  <span class="module-item-type">${esc(i.type || '')}</span>
                  ${esc(i.title || '')}
                </div>
              `).join('')}
              ${(m.items || []).length > 8 ? `<div class="module-item" style="color:var(--text-tertiary)">+ ${m.items.length - 8} more</div>` : ''}
            </div>
          </div>
        `).join('')}
      </div>
    ` : ''}
  `;
}

// ── Escape HTML ──────────────────────────────────

function esc(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

// ── Boot ─────────────────────────────────────────

async function boot() {
  // Check connection
  const status = await api('/api/status');

  if (!status?.connected) {
    switchView('setup');
    return;
  }

  state.user = status.user;

  // Show user in nav
  const navUser = $('#nav-user');
  if (status.user.avatar) {
    navUser.innerHTML = `<img class="nav-avatar" src="${esc(status.user.avatar)}" alt=""> ${esc(status.user.name)}`;
  } else {
    navUser.textContent = status.user.name;
  }

  // Load data in parallel
  const [courses, upcoming, grades] = await Promise.all([
    api('/api/courses'),
    api('/api/assignments/upcoming'),
    api('/api/grades'),
  ]);

  state.courses = courses || [];
  state.assignments = upcoming || [];
  state.grades = grades || [];

  renderDashboard();
}

// Add smart sub-headline
function updateSubHeadline() {
  const sub = $('#hero-sub');
  if (!sub) return;

  const now = new Date();
  const urgent = state.assignments.filter(a =>
    !a.submitted && a.due && daysUntil(a.due) <= 2 && daysUntil(a.due) >= 0
  );

  if (urgent.length > 0) {
    sub.textContent = `You have ${urgent.length} assignment${urgent.length > 1 ? 's' : ''} due very soon.`;
    sub.style.color = 'var(--red)';
  } else {
    const total = state.assignments.filter(a => !a.submitted).length;
    if (total === 0) {
      sub.textContent = "You're all caught up. Nice work.";
    } else {
      sub.textContent = `${total} assignment${total > 1 ? 's' : ''} on your radar.`;
    }
  }
}

// Override renderDashboard to include sub-headline update
const _origRender = renderDashboard;
// We'll call updateSubHeadline after render in boot
const origBoot = boot;

async function init() {
  await boot();
  updateSubHeadline();
}

init();
