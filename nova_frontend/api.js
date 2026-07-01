/* ===================== Nova — API client ===================== */

const NOVA_API_BASE = 'http://localhost:8000';

// Executive dev mode — signed-in user and impersonation tracking.
// Read immediately at script load so apiGet() has the header before initNova() runs.
let _novaDevUserId = sessionStorage.getItem('nova_dev_uid') || null;
let _novaImpersonateId = sessionStorage.getItem('nova_impersonate_id') || null;

function novaSetImpersonate(userId) {
  _novaImpersonateId = userId ? String(userId) : null;
}

async function apiGet(path) {
  try {
    const token = (typeof novaGetToken === 'function') ? await novaGetToken() : null;
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    if (_novaDevUserId) headers['X-Nova-Dev-User'] = _novaDevUserId;
    if (_novaImpersonateId) headers['X-Nova-Impersonate'] = _novaImpersonateId;
    const res = await fetch(NOVA_API_BASE + path, { headers });
    if (!res.ok) throw new Error(`HTTP ${res.status} for GET ${path}`);
    return await res.json();
  } catch (err) {
    console.error('[Nova API] GET', path, 'failed:', err);
    return null;
  }
}

async function apiPost(path, body) {
  try {
    const token = (typeof novaGetToken === 'function') ? await novaGetToken() : null;
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (_novaDevUserId) headers['X-Nova-Dev-User'] = _novaDevUserId;
    if (_novaImpersonateId) headers['X-Nova-Impersonate'] = _novaImpersonateId;
    const res = await fetch(NOVA_API_BASE + path, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} for POST ${path}`);
    return await res.json();
  } catch (err) {
    console.error('[Nova API] POST', path, 'failed:', err);
    return null;
  }
}

// ── Avatar gradient palette ────────────────────────────────────────────────────
// Each entry is [from, to] for a CSS linear-gradient.
// Deterministic: same name always maps to the same gradient.
const _AV_PALETTES = [
  ['#A634FF', '#FF4398'],
  ['#2ACCFF', '#5400DC'],
  ['#e08531', '#C21178'],
  ['#1FA971', '#2ACCFF'],
  ['#F588FF', '#A634FF'],
  ['#FF6B88', '#e08531'],
  ['#5400DC', '#FF4398'],
  ['#2ACCFF', '#1FA971'],
];

function _nameToGrad(name) {
  let h = 0;
  const s = (name || '').toLowerCase();
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) & 0xffff;
  }
  return _AV_PALETTES[h % _AV_PALETTES.length];
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

// Best → worst ordering and Ribbon glyph per tier (starter is never a badge).
const TIER_RANK  = { platinum: 5, diamond: 4, gold: 3, silver: 2, bronze: 1 };
const TIER_GLYPH = { platinum: 'crown', diamond: 'diamond', gold: 'star', silver: 'star', bronze: 'star' };

// Group raw badge rows [{tier, month, ...}] into per-tier columns for the UI:
// [{tier, count, color, glyph}] sorted best → worst, excluding starter.
function _mapBadges(rows) {
  const counts = {};
  (rows || []).forEach(function (b) {
    const t = b.tier;
    if (!t || t === 'starter' || !(t in TIER_RANK)) return;
    counts[t] = (counts[t] || 0) + 1;
  });
  return Object.keys(counts)
    .sort(function (a, b) { return TIER_RANK[b] - TIER_RANK[a]; })
    .map(function (t) {
      return { tier: t, count: counts[t], color: (TIER_META[t] || {}).color, glyph: TIER_GLYPH[t] };
    });
}

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
      scoredBy:  data.skills.scored_by || 'keywords',
    },
    tierScoredBy:   data.tier?.scored_by || 'keywords',
    congratsReceived: data.congrats_received ?? 0,
    badges: _mapBadges(data.badges),
    continueCourse: data.continue_course ? {
      name:     data.continue_course.name,
      cat:      'general',
      status:   'In Progress',
      progress: data.continue_course.progress,
      tile:     ['#A634FF', '#5400DC'],
    } : null,
    recommended: data.recommended ? {
      name:     data.recommended.course_name,
      meta:     data.recommended.reason,
      tile:     ['#2ACCFF', '#5400DC'],
      scoredBy: data.recommended.scored_by || 'keywords',
    } : null,
  };
}

const _RECO_TILES = [
  ['#A634FF','#5400DC'],
  ['#2ACCFF','#5400DC'],
  ['#1FA971','#2ACCFF'],
  ['#e08531','#C21178'],
];

function mapTeam(data) {
  const recSource = data.popular_source || 'team';
  return {
    recSource,
    learningTime: data.highlights.top_learner
      ? data.highlights.top_learner.credits + ' credits'
      : '—',
    highlights: {
      congrats:  data.congrats_this_week ?? 0,
      topCourse: data.top_course || '—',
      timeDelta: data.highlights?.time_delta_pct ?? 0,
    },
    accomplishments: data.accomplishments.map(a => ({
      user_id: a.user_id,
      name: a.employee_name,
      verb: 'completed',
      ach:  a.course_name,
      type: 'course',
      time: a.completed_on,
      av:   _nameToGrad(a.employee_name),
    })),
    recommended: (data.popular_courses || []).map((c, i) => ({
      name:  c.course_name,
      badge: c.category || 'Other',
      cls:   (c.category || 'other').toLowerCase(),
      meta:  recSource === 'fallback'
               ? 'Based on your learning'
               : `Completed by ${c.completion_count} teammate${c.completion_count === 1 ? '' : 's'}`,
      match: Math.max(60, 92 - i * 8),
      tile:  _RECO_TILES[i % _RECO_TILES.length],
      glyph: (c.category || 'Ot').slice(0, 2),
    })),
  };
}

function mapManager(data, teamsData, peopleData) {
  const cols = ['#A634FF','#2ACCFF','#5400DC','#FF4398','#F588FF','#e08531','#C21178','#FF6B88'];

  const aiTrendPts      = data.kpis.ai_proficiency_trend_pts ?? 0;
  const activeTrendPct  = data.kpis.active_week_trend_pct    ?? 0;
  const retRate         = data.kpis.retention_rate           ?? 0;
  const retTrendPct     = data.kpis.retention_rate_trend_pct ?? 0;
  const retTrendDir     = data.kpis.retention_rate_trend_dir ?? 'flat';
  const atRiskCount     = data.kpis.at_risk_count_company    ?? data.at_risk.length;
  const atRiskTrendPct  = data.kpis.at_risk_count_trend_pct  ?? 0;
  const atRiskTrendDir  = data.kpis.at_risk_count_trend_dir  ?? 'flat';

  const _sign = n => n >= 0 ? '+' : '';

  return {
    total:  data.kpis.total_team,
    goal:   'Every employee AI-proficient',
    target: 80,
    kpis: [
      {
        key: 'prof',
        num: data.kpis.ai_proficient_pct.toFixed(0) + '%',
        lab: `AI-proficient — <b>${data.kpis.ai_proficient_count} of ${data.kpis.total_team}</b>`,
        trend: _sign(aiTrendPts) + aiTrendPts.toFixed(1) + ' pts',
        dir:   aiTrendPts > 0 ? 'up' : aiTrendPts < 0 ? 'down' : 'flat',
        ic: 'spark',
        tint: 'rgba(166,52,255,.12)', col: '#A634FF',
      },
      {
        key: 'active',
        num: data.kpis.active_this_week.toLocaleString(),
        lab: 'Active learners <b>this week</b>',
        trend: _sign(activeTrendPct) + activeTrendPct.toFixed(0) + '%',
        dir:   activeTrendPct > 0 ? 'up' : activeTrendPct < 0 ? 'down' : 'flat',
        ic: 'users',
        tint: 'rgba(42,204,255,.14)', col: '#0f8fc4',
      },
      {
        key: 'ret',
        num: retRate.toFixed(0) + '%',
        lab: 'Active learners <b>last 30 days</b>',
        trend: _sign(retTrendPct) + retTrendPct.toFixed(0) + ' pts',
        dir:   retTrendDir,
        ic: 'shield',
        tint: 'rgba(31,169,113,.14)', col: '#1FA971',
      },
      {
        key: 'risk',
        num: atRiskCount.toString(),
        lab: 'Employees <b>at risk</b>',
        trend: _sign(atRiskTrendPct) + Math.abs(Math.round(atRiskTrendPct)) + '%',
        dir:   atRiskTrendDir,
        badWhenUp: true,   // at-risk going up is bad → invert the trend chip color
        ic: 'alert',
        tint: 'rgba(226,61,110,.12)', col: '#E23D6E',
      },
    ],
    months: data.monthly_trend.map(m => m.month),
    series: {
      proficiency: data.monthly_trend.map(m => m.credits),
      active:      data.monthly_trend.map(m => m.active_pct ?? 0),
    },
    distribution: [],
    teams: teamsData.departments.map((d, i) => {
      const pct      = d.ai_proficient_pct;
      const trendPct = d.trend_pct ?? 0;
      return {
        name:    d.name,
        members: d.headcount,
        prof:    Math.round(pct),
        trend:   _sign(trendPct) + Math.round(trendPct) + '%',
        dir:     trendPct > 0 ? 'up' : trendPct < 0 ? 'down' : 'flat',
        status:  pct >= 70 ? 'ok' : pct >= 50 ? 'warn' : 'risk',
        col:     cols[i % cols.length],
      };
    }),
    people: peopleData.employees.map(e => ({
      user_id:     e.user_id,
      name:        e.name,
      role:        e.department,
      team:        e.department,
      department:  e.department,
      designation: e.designation || '',
      tier:        e.tier,
      prof:        Math.round(e.ai_proficiency),
      status:      Math.round(e.ai_proficiency) < 20 ? 'risk' : 'ok',
      av:          _nameToGrad(e.name),
      scoredBy:    e.scored_by || 'keywords',
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
    // Restore dev mode headers before the first API call
    const devUid = sessionStorage.getItem('nova_dev_uid');
    const impersonateId = sessionStorage.getItem('nova_impersonate_id');

    // Always set the signed-in dev user separately from impersonation target
    if (devUid) {
      _novaDevUserId = devUid;
    }
    if (impersonateId) {
      if (typeof novaSetImpersonate === 'function') {
        novaSetImpersonate(impersonateId);
      }
    } else {
      // Clear any stale impersonation
      if (typeof novaSetImpersonate === 'function') {
        novaSetImpersonate(null);
      }
    }

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

    // Store the role globally so app.jsx can use it for tab rendering
    window.NOVA.accounts.role = role;

    if (role === 'employee') {
      window.NOVA.accounts.manager = null;
      const [dashData, teamData] = await Promise.all([
        apiGet('/api/employee/dashboard'),
        apiGet('/api/employee/team'),
      ]);
      if (dashData) window.NOVA.employee = mapDashboard(dashData);
      if (teamData)  window.NOVA.team    = mapTeam(teamData);

      console.log('[Nova] Real data loaded for role:', role);
      _resolveDataReady();

    } else if (role === 'manager') {
      window.NOVA.accounts.employee = null;
      const [overviewData, teamsData, peopleData] = await Promise.all([
        apiGet('/api/manager/overview'),
        apiGet('/api/manager/teams'),
        apiGet('/api/manager/people?filter=all'),
      ]);
      if (overviewData && teamsData && peopleData) {
        window.NOVA.manager = mapManager(overviewData, teamsData, peopleData);
      }

      console.log('[Nova] Real data loaded for role:', role);
      _resolveDataReady();

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

      console.log('[Nova] Real data loaded for role:', role);
      _resolveDataReady();
    }
  } catch (err) {
    console.warn('[Nova] API unavailable:', err.message);
    _resolveDataReady(); // unblock app.jsx; AppRoot will show the error screen
  }
}

// Expose gradient helper for use in JSX files loaded after api.js
window._nameToGrad = _nameToGrad;

// Delay initNova() slightly so the sign-in gate in app.jsx can check MSAL account first
setTimeout(initNova, 100);
