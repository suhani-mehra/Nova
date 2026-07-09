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
  silver:   { name: 'Silver',   color: '#7EC8E3', tile: ['#7EC8E3', '#4FA8C9'] },
  gold:     { name: 'Gold',     color: '#f5b71e', tile: ['#f5b71e', '#e08531'] },
  diamond:  { name: 'Diamond',  color: '#0a2473', tile: ['#0a2473', '#5400DC'] },
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
      match: (c.match_pct != null) ? c.match_pct : Math.max(60, 92 - i * 8),
      tile:  _RECO_TILES[i % _RECO_TILES.length],
      glyph: (c.category || 'Ot').slice(0, 2),
    })),
  };
}

// Region colors for the "AI proficiency by region" bar chart. Reuses the app's
// existing palette (matches the line-chart legend gradient stops).
const REGION_COLORS = {
  asia:  '#FF4398',
  na:    '#2ACCFF',
  eu:    '#A634FF',
};
const REGION_KEYS = ['asia', 'na', 'eu'];

// Shapes the backend's proficiency_by_region payload into a chart-ready object.
// Returns null when the payload is missing or has no employees (background job
// still warming) so the UI can show a graceful "computing" state.
function mapProficiencyByRegion(pbr) {
  if (!pbr || !pbr.levels || !pbr.total) return null;
  const labels = pbr.region_labels || {};
  // Only include regions that actually have employees, in the canonical order.
  const regions = REGION_KEYS
    .filter(k => (pbr.region_totals && pbr.region_totals[k]) > 0)
    .map(k => ({
      key:   k,
      label: labels[k] || k,
      color: REGION_COLORS[k] || '#9aa2b1',
      total: pbr.region_totals[k],
    }));
  return {
    total:   pbr.total,
    regions,
    regionTotals: pbr.region_totals || {},
    levels: pbr.levels.map(lv => ({
      key:        lv.key,
      name:       lv.name,
      threshold:  lv.threshold,
      goalPct:    lv.goal_pct,
      totalPct:   lv.total_pct,
      totalCount: lv.total_count,
      regions:    lv.regions || {},   // {asia:{count,pct_of_company,pct_of_region,label}, ...}
    })),
  };
}

// Maps a direct-reports employee row (from /api/manager/your-team or
// /api/manager/people/search) into the shape the people table expects.
function _mapPersonRow(e) {
  return {
    user_id:     e.user_id,
    name:        e.name,
    role:        e.designation || e.department,
    team:        e.department,
    department:  e.department,
    designation: e.designation || '',
    tier:        e.tier,
    prof:        Math.round(e.ai_proficiency),
    streak:      e.streak_days || 0,
    status:      Math.round(e.ai_proficiency) < 20 ? 'risk' : 'ok',
    av:          _nameToGrad(e.name),
    scoredBy:    e.scored_by || 'keywords',
  };
}

// Company-wide Overview (exec managers only). data = /api/manager/overview.
function mapOverview(data) {
  const total          = data.kpis.total_team;
  const activeWeek     = data.kpis.active_this_week ?? 0;
  const activeTrendPct = data.kpis.active_week_trend_pct ?? 0;
  const activePct      = total ? (activeWeek / total * 100) : 0;
  const _sign = n => n >= 0 ? '+' : '';

  return {
    total,
    target: 80,
    months: data.monthly_trend.map(m => m.month),
    series: {
      proficiency: data.monthly_trend.map(m => m.credits),
      active:      data.monthly_trend.map(m => m.active_pct ?? 0),
    },
    proficiencyByRegion: mapProficiencyByRegion(data.proficiency_by_region),
    // Active-learners-this-week hero card (the only surviving KPI).
    activeLearners: {
      count:    activeWeek,
      pct:      activePct,
      total,
      trendPct: activeTrendPct,
      trendStr: _sign(activeTrendPct) + Math.round(activeTrendPct) + '% vs last week',
    },
    // Real per-department proficiency (name + %), sorted best-first by backend.
    teamLeaderboard: (data.team_leaderboard || []).map(t => ({
      name: t.name,
      prof: Math.round(t.prof),
    })),
  };
}

// Direct-reports team view (all managers). data = /api/manager/your-team.
function mapYourTeam(data) {
  const people = (data.employees || []).map(_mapPersonRow);
  const b = data.badges || {};
  const teamSize = data.team_size != null ? data.team_size : people.length;
  const activeCount = data.active_this_week || 0;
  return {
    size:   people.length,
    activeThisWeek: {
      count: activeCount,
      total: teamSize,
      pct:   teamSize ? (activeCount / teamSize * 100) : 0,
    },
    coursesThisWeek: data.courses_this_week || 0,
    radar:  data.radar || {axes:['AI','Cloud','Frontend','Backend','Data'], this_month:[0,0,0,0,0], last_month:[0,0,0,0,0]},
    badges: {
      total:          b.total || 0,
      avgPerPerson:   b.avg_per_person || 0,
      thisMonthCount: b.this_month_count || 0,
      byTier:         b.by_tier || {platinum:0,diamond:0,gold:0,silver:0,bronze:0},
    },
    people,
    riskCount: people.filter(p => p.status === 'risk').length,
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
    isExecManager: !!data.is_exec_manager,
    colorMode: data.color_mode || 'light',
  };
}

// ── Boot ──────────────────────────────────────────────────────────────────────

// Apply a color mode app-wide: stamp <html data-theme> (CSS palette hook) and
// cache it so the next load is flash-free. Global so app.jsx + api.js share it.
function applyTheme(mode) {
  const m = mode === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = m;
  try { localStorage.setItem('nova_theme', m); } catch (e) {}
}
window.applyTheme = applyTheme;

// Persist the account's color mode to the backend (fire-and-forget).
async function saveColorMode(mode) {
  try {
    await apiPost('/api/me/color-mode', { mode });
  } catch (e) { console.warn('[Nova] save color mode failed:', e); }
}
window.saveColorMode = saveColorMode;

let _resolveDataReady;
window.__novaDataReady = new Promise(function(resolve) { _resolveDataReady = resolve; });

// Loads manager-area data into NOVA.manager. "Your Team" is fetched for every
// manager; the company-wide Overview is fetched only for exec managers (avoids a
// needless 403). Static placeholder sections (verticals, specialization) come
// from data.js — see NOVA.managerStatic.
async function loadManagerData(isExec) {
  const fetches = [apiGet('/api/manager/your-team?filter=all')];
  if (isExec) fetches.push(apiGet('/api/manager/overview'));
  const [teamData, overviewData] = await Promise.all(fetches);

  const M = { isExec: !!isExec, team: null, overview: null };
  if (teamData) {
    try { M.team = mapYourTeam(teamData); }
    catch (e) { console.warn('[Nova] mapYourTeam failed:', e); }
  }
  if (isExec && overviewData) {
    try { M.overview = mapOverview(overviewData); }
    catch (e) { console.warn('[Nova] mapOverview failed:', e); }
  }
  // Static placeholder sections (no real data source yet) — see data.js.
  M.static = window.NOVA.managerStatic || {};
  window.NOVA.manager = M;
}

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
    // Exec managers (company-wide Overview tab) — everyone else sees Your Team only.
    window.NOVA.accounts.isExecManager = !!meData.is_exec_manager;
    // Apply the account's saved color mode (authoritative over the pre-boot
    // localStorage guess) and cache it for a flash-free next load.
    window.NOVA.accounts.colorMode = meData.color_mode || 'light';
    if (typeof applyTheme === 'function') applyTheme(window.NOVA.accounts.colorMode);

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
      await loadManagerData(meData.is_exec_manager);
      console.log('[Nova] Real data loaded for role:', role);
      _resolveDataReady();

    } else if (role === 'both') {
      const [dashData, teamData] = await Promise.all([
        apiGet('/api/employee/dashboard'),
        apiGet('/api/employee/team'),
      ]);
      if (dashData) {
        try { window.NOVA.employee = mapDashboard(dashData); }
        catch(e) { console.warn('[Nova] mapDashboard failed:', e); }
      }
      if (teamData) {
        try { window.NOVA.team = mapTeam(teamData); }
        catch(e) { console.warn('[Nova] mapTeam failed:', e); }
      }
      await loadManagerData(meData.is_exec_manager);
      window.dispatchEvent(new CustomEvent('nova-manager-ready'));

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
