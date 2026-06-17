/* ===================== Nova — API client ===================== */

const NOVA_API_BASE = 'http://localhost:8000';

async function apiGet(path) {
  try {
    const res = await fetch(NOVA_API_BASE + path);
    if (!res.ok) throw new Error(`HTTP ${res.status} for GET ${path}`);
    return await res.json();
  } catch (err) {
    console.error('[Nova API] GET', path, 'failed:', err);
    return null;
  }
}

async function apiPost(path, body) {
  try {
    const res = await fetch(NOVA_API_BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} for POST ${path}`);
    return await res.json();
  } catch (err) {
    console.error('[Nova API] POST', path, 'failed:', err);
    return null;
  }
}

// ── Tier metadata ─────────────────────────────────────────────────────────────

const TIER_META = {
  starter:  { name: 'Starter',  color: '#9aa2b1', tile: ['#9aa2b1', '#6b7280'] },
  bronze:   { name: 'Bronze',   color: '#e08531', tile: ['#e08531', '#b85e1a'] },
  silver:   { name: 'Silver',   color: '#9aa3af', tile: ['#9aa3af', '#6b7280'] },
  gold:     { name: 'Gold',     color: '#f5b71e', tile: ['#f5b71e', '#e08531'] },
  diamond:  { name: 'Diamond',  color: '#2ACCFF', tile: ['#2ACCFF', '#5400DC'] },
  platinum: { name: 'Platinum', color: '#A634FF', tile: ['#A634FF', '#FF4398'] },
};

// ── Response mappers ──────────────────────────────────────────────────────────

function mapDashboard(data) {
  return {
    currentTier:  data.tier.current,
    nextTier:     data.tier.next,
    tierProgress: data.tier.progress,
    learningTime: data.streak.learning_time,
    streak:       data.streak.current,
    streakWeek:   data.streak.week_map,
    skills: {
      axes:      data.skills.axes,
      thisMonth: data.skills.this_month,
      lastMonth: data.skills.last_month,
      delta:     data.skills.delta,
    },
    badges: data.badges,
    continueCourse: data.continue_course ? {
      name:     data.continue_course.name,
      cat:      'general',
      status:   'In Progress',
      progress: data.continue_course.progress,
      tile:     ['#A634FF', '#5400DC'],
    } : null,
    recommended: data.recommended ? {
      name: data.recommended.course_name,
      meta: data.recommended.reason,
      tile: ['#2ACCFF', '#5400DC'],
    } : null,
  };
}

function mapTeam(data) {
  return {
    learningTime: data.highlights.top_learner
      ? data.highlights.top_learner.credits + ' credits'
      : '—',
    highlights: {
      congrats:  0,
      topCourse: data.popular_courses[0]?.course_name || '—',
      timeDelta: 0,
    },
    accomplishments: data.accomplishments.map(a => ({
      name: a.employee_name,
      verb: 'completed',
      ach:  a.course_name,
      type: 'course',
      time: a.completed_on,
      av:   ['#A634FF', '#FF4398'],
    })),
    recommended: data.popular_courses.map(c => ({
      name:  c.course_name,
      badge: c.category,
      cls:   c.category.toLowerCase(),
      meta:  `Completed by ${c.completion_count} teammates`,
      match: 75,
      tile:  ['#2ACCFF', '#5400DC'],
      glyph: c.category.slice(0, 2),
    })),
  };
}

function mapManager(data, teamsData, peopleData) {
  const cols = ['#A634FF','#2ACCFF','#5400DC','#FF4398','#F588FF','#e08531','#C21178','#FF6B88'];
  return {
    total:  data.kpis.total_team,
    goal:   'Every employee AI-proficient',
    target: 80,
    kpis: [
      {
        key: 'prof',
        num: data.kpis.ai_proficient_pct.toFixed(0) + '%',
        lab: `AI-proficient — <b>${data.kpis.ai_proficient_count} of ${data.kpis.total_team}</b>`,
        trend: '+0 pts', dir: 'up', ic: 'spark',
        tint: 'rgba(166,52,255,.12)', col: '#A634FF',
      },
      {
        key: 'active',
        num: data.kpis.active_this_week.toLocaleString(),
        lab: 'Active learners <b>this week</b>',
        trend: '+0%', dir: 'up', ic: 'users',
        tint: 'rgba(42,204,255,.14)', col: '#0f8fc4',
      },
      {
        key: 'ret', num: '—',
        lab: 'Learning <b>retention rate</b>',
        trend: '—', dir: 'flat', ic: 'shield',
        tint: 'rgba(31,169,113,.14)', col: '#1FA971',
      },
      {
        key: 'risk',
        num: data.at_risk.length.toString(),
        lab: 'Employees <b>falling behind</b>',
        trend: '—', dir: 'down', ic: 'alert',
        tint: 'rgba(226,61,110,.12)', col: '#E23D6E',
      },
    ],
    months: data.monthly_trend.map(m => m.month),
    series: {
      proficiency: data.monthly_trend.map(m => m.credits),
      retention:   data.monthly_trend.map(m => 0),
    },
    distribution: [],
    teams: teamsData.departments.map((d, i) => {
      const pct = d.ai_proficient_pct;
      return {
        name:    d.name,
        members: d.headcount,
        prof:    Math.round(pct),
        trend:   '+0%',
        dir:     'flat',
        status:  pct >= 70 ? 'ok' : pct >= 50 ? 'warn' : 'risk',
        col:     cols[i % cols.length],
      };
    }),
    people: peopleData.employees.map(e => ({
      name:   e.name,
      role:   e.department,
      team:   e.department,
      tier:   e.tier,
      prof:   Math.round(e.ai_proficiency),
      trend:  '+0%',
      dir:    e.status === 'thriving' ? 'up' : e.status === 'at_risk' ? 'down' : 'flat',
      status: e.status === 'thriving' ? 'ok'  : e.status === 'at_risk' ? 'risk' : 'warn',
      av:     ['#A634FF', '#FF4398'],
    })),
  };
}

function mapMe(data) {
  const avByRole = {
    manager:  ['#2ACCFF', '#5400DC'],
    both:     ['#2ACCFF', '#A634FF'],
    employee: ['#A634FF', '#FF4398'],
  };
  return {
    id:    data.user_id || 'dev',
    name:  data.name,
    first: data.name.split(' ')[0],
    role:  data.designation_code || data.role,
    email: data.email,
    team:  data.department_code || 'Nova',
    av:    avByRole[data.role] || avByRole.employee,
    kind:  data.role,  // passes through "employee" | "manager" | "both"
  };
}

// ── Boot ──────────────────────────────────────────────────────────────────────

let _resolveDataReady;
window.__novaDataReady = new Promise(function(resolve) { _resolveDataReady = resolve; });

async function initNova() {
  try {
    const meData = await apiGet('/api/me');
    if (!meData) throw new Error('Could not fetch /api/me');

    const user = mapMe(meData);
    const role = meData.role;

    if (window.NOVA && window.NOVA.accounts) {
      if (role === 'both') {
        // Same person, two views — build separate employee and manager account objects.
        window.NOVA.accounts.employee = Object.assign({}, user, { av: ['#A634FF','#FF4398'], kind: 'employee' });
        window.NOVA.accounts.manager  = Object.assign({}, user, { av: ['#2ACCFF','#5400DC'], kind: 'manager' });
        window.NOVA.accounts.current  = window.NOVA.accounts.employee;
      } else {
        window.NOVA.accounts[role]   = user;
        window.NOVA.accounts.current = user;
      }
    }

    if (role === 'employee') {
      const [dashData, teamData] = await Promise.all([
        apiGet('/api/employee/dashboard'),
        apiGet('/api/employee/team'),
      ]);
      if (dashData) window.NOVA.employee = mapDashboard(dashData);
      if (teamData)  window.NOVA.team    = mapTeam(teamData);

    } else if (role === 'manager') {
      const [overviewData, teamsData, peopleData] = await Promise.all([
        apiGet('/api/manager/overview'),
        apiGet('/api/manager/teams'),
        apiGet('/api/manager/people?filter=all'),
      ]);
      if (overviewData && teamsData && peopleData) {
        window.NOVA.manager = mapManager(overviewData, teamsData, peopleData);
      }

    } else if (role === 'both') {
      // Load all data concurrently — thread-local connections handle parallelism safely.
      const [dashData, teamData, overviewData, teamsData, peopleData] = await Promise.all([
        apiGet('/api/employee/dashboard'),
        apiGet('/api/employee/team'),
        apiGet('/api/manager/overview'),
        apiGet('/api/manager/teams'),
        apiGet('/api/manager/people?filter=all'),
      ]);

      // Map each dataset independently so one failure doesn't block the rest.
      if (dashData) {
        try { window.NOVA.employee = mapDashboard(dashData); }
        catch(e) { console.warn('[Nova] mapDashboard failed:', e); }
      }
      if (teamData) {
        try { window.NOVA.team = mapTeam(teamData); }
        catch(e) { console.warn('[Nova] mapTeam failed:', e); }
      }
      if (overviewData && teamsData && peopleData) {
        try {
          window.NOVA.manager = mapManager(overviewData, teamsData, peopleData);
          window.dispatchEvent(new CustomEvent('nova-manager-ready'));
        }
        catch(e) { console.warn('[Nova] mapManager failed:', e); }
      } else {
        console.warn('[Nova] manager fetch incomplete — overview:', !!overviewData, 'teams:', !!teamsData, 'people:', !!peopleData);
      }
    }

    console.log('[Nova] Real data loaded for role:', role);
  } catch (err) {
    console.warn('[Nova] API unavailable, falling back to dummy data:', err.message);
  } finally {
    _resolveDataReady(); // always unblock app.jsx, even on error
  }
}

initNova();
