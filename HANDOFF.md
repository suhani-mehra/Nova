# Nova — Project Handoff

## 1. Purpose

**Nova** (by Orion Innovation) is an internal learning-analytics dashboard that turns raw
training activity from Orion's learning platform ("Classmate") into a motivating,
game-like view of each employee's AI/tech upskilling — and gives managers a company-wide
picture of AI proficiency.

It has two audiences, served from one app:

- **Employee view ("My Progress" / "My Team")** — your monthly tier, learning streak, a
  5-axis skill radar (AI/Cloud/Frontend/Backend/Data), badges earned each month, a
  "continue learning" / recommended course, and what your teammates have accomplished
  (with the ability to send "congrats").
- **Manager view — two tabs:**
  - **Overview** (company-wide, **exec managers only**): an AI-proficiency trend chart by
    quarter with a second "by region" tab (four proficiency levels split by
    Asia/North America/Europe/Other), a Proficiency-by-Vertical chart, an Active-learners-this-week
    card, a Specialization Landscape, and a per-department Team Leaderboard.
  - **Your Team** (direct reports only, **any manager**): a team-average skill radar, a badges
    donut, active-learners + courses-completed this week, and a searchable people list with each
    person's tier, proficiency, and learning streak.

The whole app is **light or dark**, a per-account preference toggled from the profile menu and
persisted server-side (see §5).

The core idea: an LLM grades every course on 5 skill verticals; each employee's completed
courses roll up into skill scores and a composite **tier**; tiers reset monthly and mint a
**badge** at month end so progress is visible and competitive.

---

## 2. Architecture & Stack

- **Backend:** FastAPI (Python), served by uvicorn on port 8000. Async endpoints offload
  blocking DB work to a thread pool.
- **Frontend:** Vanilla React 18 via CDN + in-browser Babel (no build step). JSX files are
  transpiled in the browser. Served as static files by the same FastAPI app (mounted with a
  no-store cache header so edits show on reload). Lives in `nova_frontend/`.
- **System of record:** **Microsoft Fabric Data Warehouse** (the "Classmate" schema),
  accessed via `pyodbc` with an Azure AD access token. This is read-only source data,
  refreshed nightly by an upstream ETL (~12–2 AM; last day of a month lands ~1 AM the next
  day).
- **Local cache / app state:** **SQLite** (`nova_backend/nova_local.db`) — holds all
  computed/derived data so requests never wait on Fabric. Also stores app-owned data
  (badges, congrats).
- **LLM:** Azure OpenAI **gpt-4o-mini** — used to (a) grade courses on the 5 verticals and
  (b) pick a recommended course.
- **Auth:** Azure AD (JWT bearer) in production; a **dev bypass** loads a fixed user
  locally.

Data flow: **Fabric (raw activity) → Python computes scores/tiers/KPIs → SQLite cache →
FastAPI endpoints → frontend mappers → `window.NOVA` → React views.**

---

## 3. Source Data (Fabric "classmate" schema)

Key tables and the **status codes** that matter:

- `vw_classmate_trainings` — course enrollments/completions. **`status = 4052` = completed**,
  **`status = 4035` = in progress**. Has `learning_credits`, `completed_on`,
  `second_level_category_id` (the course/category id), `course_name`.
- `fact_classmate_learning_credit` — granular learning-credit events with `value`,
  `credit_date`, `duration`, and `topic` (for self-study/session/recorded items).
- `fact_classmate_certification` — certifications (**`status = 2` = completed**).
- `fact_classmate_user_skill_status` / `fact_classmate_self_study` — extra activity signals
  (used for "active day" detection; self-study **`status = 2` = attended**).
- `dim_classmate_employee_profile` — org data (manager, department_code, designation,
  display_name, `country_code`). This is a **slowly-changing dimension with many rows per
  person**, so every read must deduplicate to the latest row per `user_id` (via a
  `ROW_NUMBER() … ORDER BY modified_on DESC` CTE — the `_DEDUP_CTE`). Forgetting this fans out
  ~73k rows for ~13k people. `country_code` is a non-ISO Classmate code (e.g. `IND`, `SER`,
  `UZKH`) — see `core/geo.py` for the confirmed mapping to region.
- `dim_classmate_user` — identity (email/`aduser_name`, names).
- `dim_classmate_second_level_category` / `dim_classmate_certificate` — the course/cert
  catalog (source of items to grade).

Common filters: `is_active=1 AND is_deleted=0 AND etl_isactive=1`.

---

## 4. Local SQLite cache (`nova_local.db`)

Six tables. Three are **derived caches** (safe to purge/rebuild); three are **app-owned data**
(must persist).

| Table | Kind | Contents |
|---|---|---|
| `gpt_cache` | derived | Generic key→JSON cache with per-row expiry. Holds skill radars (`classify_{uid}`), tier dicts (`tier_{uid}`), company stats, trends, team lists, streaks, etc. |
| `course_vertical_scores` | derived (slow to rebuild) | The LLM's per-course 5-vertical grades. ~24.5k rows. |
| `user_tier_scores` | derived | The percentile-ranking population: `{user_id → composite tier_score}` for the current month. |
| `user_badges` | **app-owned** | Monthly badges: `(user_id, tier, month 'YYYY-MM', awarded_at)`, `UNIQUE(user_id, month)`. |
| `congrats` | **app-owned** | Peer congratulations: `(sender, receiver, activity_id, message, created_at)`. |
| `user_settings` | **app-owned** | Per-account preferences: `(user_id PK, color_mode 'light'|'dark', updated_at)`. See `nova_db/user_settings.py`. |

`gpt_cache` is a simple table: `cache_key`, `result` (JSON), `scored_by`, `expires_at`.
Helpers: `get_cache` (respects expiry), `get_cache_stale` (ignores expiry, for
stale-while-revalidate), `set_cache(key, result, scored_by, ttl_hours)`,
`clear_expired`, `clear_by_prefix`.

---

## 5. Authentication & dev bypass

- **Production:** `Authorization: Bearer <Azure AD JWT>`. The token is validated against the
  tenant's JWKS (RS256, audience = client id, issuer = tenant v2.0). The email claim
  (`preferred_username`/`upn`) is looked up in `dim_classmate_user` to get the
  `classmate_user_id`. Role is derived from whether the person has direct reports.
- **Dev bypass** (`NOVA_DEV_BYPASS=true`, or tenant set to `placeholder`): no JWT required.
  Loads **Pradeep Menon (user_id 5575)** by default. Role is `"both"` because he has reports
  (so you can see employee and manager views).
- **Role** is one of `"employee"`, `"manager"`, `"both"` and drives which tabs/data load.
- **Exec managers** are a separate, narrower concept layered on top of role. `EXEC_USER_IDS`
  in `routers/manager.py` (`{5575, 16467, 16465, 16470}`, extended at startup from
  `EXEC_USER_NAMES` via `_init_exec_users()`) governs company-wide access. The Overview tab
  and `/api/manager/overview` are gated by `_is_exec_manager(user)` = *in `EXEC_USER_IDS`
  **and** actually a manager* (`role in {manager, both}`). Since the non-Pradeep IDs aren't
  managers, this resolves to **Pradeep Menon (5575) only** today. `/api/me` surfaces this as
  `is_exec_manager` so the frontend shows/hides the Overview tab. `RECURSIVE_USER_IDS` (`{5575}`)
  additionally unlocks recursive-org people search for that user.
- **Dev impersonation:** the *same* exec IDs can pass `X-Nova-Dev-User` (sign in as any user)
  or `X-Nova-Impersonate` (view as another user). The frontend stores these in sessionStorage
  and only shows the impersonation panel for `16467/16465/16470`.
- **Color mode (light/dark):** a per-account preference stored in `user_settings.color_mode`
  (`nova_db/user_settings.py`). `/api/me` returns `color_mode`; the profile-menu toggle writes
  it via `POST /api/me/color-mode`. The frontend stamps `<html data-theme>` (see §8) and caches
  the last value in `localStorage` (`nova_theme`) for a flash-free boot before `/api/me`
  reconciles the authoritative account value.
- **Fabric connection:** a single process-wide `InteractiveBrowserCredential` acquires an
  Azure token (scope `https://database.windows.net/.default`); the token is injected into
  `pyodbc.connect(..., attrs_before={1256: token_struct})`. Connections are per-thread and
  refresh every 55 min (before the 60-min token expiry). `query()` retries transient
  SQLSTATE errors (`08S01`, `08001`, `HYT00`, `HY000`) up to twice.

---

## 6. Core calculations

### 6.1 LLM course grading (the foundation)

A background job (`score_all_courses`) grades **every course, certification, and
learning-credit topic** on the 5 verticals **AI, Cloud, Frontend, Backend, Data**, each
**0–100**:

- 80–100 = primarily this vertical, 50–79 = significant, 20–49 = some, 1–19 = minimal, 0 =
  none.
- Model: gpt-4o-mini, temperature 0, JSON output, batches of 25 items.
- **Incremental:** only items not already in `course_vertical_scores` are sent to the LLM
  (safe to re-run). Each item keyed by `(item_type, item_id)` where `item_type ∈
  {course, cert, lc}`; learning-credit topics use `crc32(topic)` as a stable id.
- **Fallback** (`_classify`, keyword matching): if the LLM fails, a course maps to a single
  best-guess vertical scored 70, others 0.

### 6.2 Skill scores & the radar (per employee)

For an employee, take all completed items (courses + certs + LC topics), look up each item's
5-vertical grades, and **sum them per axis** (additive raw score). Then normalize each axis
with a **power curve**:

```
axis_score = min(100, (raw_axis_sum / 5000) ^ 0.4 * 100)
```

- Constants: `MASTERY_THRESHOLD = 5000`, `MASTERY_POWER = 0.4`. The curve gives fast early
  progress but makes 100 very hard (≈5000 raw points). Missing/ungraded → 0 (never a gifted
  default).
- **Radar "this month" vs "last month":** `this_month` = normalized cumulative **all-time**;
  `last_month` = normalized cumulative up to the **start of the current calendar month**. So
  when a new month begins (and the nightly job recomputes), `last_month` naturally becomes
  "where you ended last month," giving a real comparison. Skill is **long-term** — it does
  **not** reset monthly.
- Cached per user as `classify_{uid}`. A **`queries_ok` guard** prevents caching all-zero
  scores when a Fabric query fails transiently (which would otherwise poison the cache for
  24h).

### 6.3 AI proficiency

"AI proficiency" for a person = their normalized **AI axis** score (0–100). A person is
counted **"AI-proficient"** if that score `≥ ai_proficiency_min_score` (**30**). This single
threshold + the `^0.4` curve is used **consistently** for the Teams page, the manager
direct-reports count, and the Overview trend chart (they were unified so they agree).

**Proficiency levels (Overview "by region" chart):** four named, **cumulative** ("at least")
bands on that same AI axis score — `professional ≥30` (same threshold as above), `specialist
≥45`, `expert ≥55`, `champion ≥65` (`settings.ai_proficiency_levels`). Cumulative means a
Champion also counts toward Professional/Specialist/Expert. Each level has a hardcoded
coverage **goal** (`settings.ai_proficiency_level_goals`: 80/50/35/20%) shown as a dashed
target line on the chart — edit these two config dicts to retune either the level cutoffs or
the goals.

### 6.4 Tier (MONTHLY, percentile-ranked)

Each employee gets a composite **`tier_score`** (0–100), a weighted blend:

```
tier_score = 0.30 * credits_score
           + 0.35 * skill_score
           + 0.20 * consistency_score
           + 0.15 * recency_score
```

The tier is a **monthly competition** — three of the four components window to the **current
calendar month** and reset on the 1st; **skill stays all-time** (a long-term anchor/floor):

- `credits_score` = `min(month-to-date completed credits / monthly_credit_target * 100, 100)`
  (`monthly_credit_target = 100`).
- `consistency_score` = `min(distinct active days this month / days_in_month * 100, 100)`
  (active day = any learning-credit w/ duration, skill-status update, or attended self-study
  that day).
- `recency_score` = `min(month-to-date learning-credit value / monthly_company_avg / 3 *
  100, 100)` (a person at the company monthly average scores ~33; 3× average = 100).
- `skill_score` = average of the 5 all-time normalized axis scores.

**Percentile → tier:** every active learner's `tier_score` is ranked company-wide. Your
percentile (share of the population scoring higher than you; **lower = better**) maps to a
tier via cumulative cutoffs:

| Tier | Percentile |
|---|---|
| Platinum | ≤ 3% |
| Diamond | ≤ 10% |
| Gold | ≤ 20% |
| Silver | ≤ 40% |
| Bronze | ≤ 60% |
| Starter | otherwise (bottom 40%) |

`tier_progress` (0–100) is how far you are through your current band, used for the progress
bar. The ranking **population** is "all-time active learners" (anyone who ever completed
training) — kept stable so early-month percentiles don't blow up; people with no activity
this month simply rank near the bottom (the intended monthly reset).

**Single source of truth:** tiers are computed in one place and cached as `tier_{uid}`. Both
the employee dashboard and the manager People/Teams views are **pure cache reads** of that
key, so they never disagree. (Consequence of monthly tiers: the manager tier column is also
monthly — early each month most people show low and climb over the month.)

### 6.5 Monthly badge awarding

At the **nightly refresh on the 1st of the month** (the first cache run with the just-ended
month's complete data), each employee is awarded a badge equal to the tier they **ended the
completed month** with:

- Computes that month's **final** tiers (windowed to the prior month) **without** touching
  the live tier cache, ranks them, and writes one `user_badges` row per person.
- **`starter` is never awarded.** Awarding is **idempotent** (`UNIQUE(user_id, month)`), so
  re-runs are no-ops; a `day ≤ 3` backfill covers a missed run.
- After awarding, the normal refresh computes the **new** month's tiers → the live tier
  resets.

**Badge display:** the frontend groups a user's badge rows by tier, sorts **best→worst**
(platinum→bronze), and renders one **column per tier** with repeats **stacked vertically**;
if a column exceeds the visible slots (`MAX_STACK = 3`) it shows a **"+N"** chip. Colors come
from tier metadata; glyphs: platinum=crown, diamond=diamond, gold/silver/bronze=star (color
distinguishes them).

### 6.6 Streak

Union three activity sources per day (learning credit w/ duration, skill-status update,
attended self-study). **Current streak** = consecutive active days ending today (or
yesterday if today isn't active yet). **Week map** = 7 booleans Mon–Sun for the current
week. **Learning time this week** = `SUM(duration)` this week formatted `"Xh Ym"`. Cached as
`streak_{uid}`.

### 6.7 Recommended course

Builds context (recent completions, in-progress course, two weakest skill axes, courses
popular on the manager's team, and the catalog of not-yet-completed active courses),
prefers weak-skill courses, and asks the LLM to pick one with a short reason. Falls back to
the first uncompleted catalog course if the LLM fails. Cached as `recommend_{uid}`.

### 6.8 Team view (employee side)

- **Highlights:** top learner (most credits this month), most improved (biggest this-month
  vs last-month delta), streak leader; plus congrats-this-week count and a "team learning vs
  last week" percentage (change in completions this week vs last).
- **Accomplishments:** recent (last 14 days) completed courses by the person's team (peers +
  their own reports), each tagged with a vertical category.
- **Recommended-for-you / most-completed:** most-completed team courses the user hasn't done,
  with **training/compliance modules filtered out** (keyword list → all-zero vertical scores
  → keyword classifier).

### 6.9 Manager Overview (company-wide, exec managers only)

Served by `GET /api/manager/overview` (403 unless `_is_exec_manager`). Contents:

- **Active learners this week:** the one surviving headline KPI (a gradient hero card). Count
  of active employees with ≥1 active day this week, computed **directly from Fabric in one
  query** using the 3-source activity union (learning credit w/ duration, skill-status update,
  attended self-study) restricted to active employees — `_get_company_active_this_week()`. It
  does **not** depend on per-user `streak_{uid}` caches being warm (an earlier version read
  those and reported 0 at cold start). Trend = current count vs a weekly baseline
  (`company_active_prev`, 7-day TTL). `total_team` (company headcount) drives the "% of N"
  denominator.

**AI-proficiency trend chart (Trend tab):** builds 6 completed quarters (plus a hidden warm-up
quarter for context). Per quarter, each employee's cumulative AI raw (up to that quarter's end)
is normalized with the same `^0.4` curve and compared to the `≥30` threshold; the line is the
% of employees proficient. A second dashed line shows **% active learners** per quarter. The
**current, in-progress quarter is excluded** (the chart is "measured at quarter end") so the
line doesn't dip on partial data. Target line drawn at 80%.

**AI-proficiency by region (By region tab):** a **grouped** bar chart, one group per proficiency
level (Professional → Champion) with one bar per region (Asia / North America / Europe / Other).
Each bar's height = **that region's own % proficient** at the level (independent of headcount, so
a large region doesn't visually swamp the rest); a dashed line marks the company-wide goal and a
solid grey tick marks the company-wide actual. `_compute_ai_proficiency_by_region()` reuses the
same warm `classify_{uid}` AI-score cache as the dept snapshot (no rescoring), maps each
employee's `country_code` to a region via `core/geo.py`, and counts per level/region. Region
membership comes from real, confirmed Fabric data (no invented "unmapped" catch-all — every
country_code present resolves to Asia/NA/Europe/Other per an explicit mapping agreed with the
product owner, including a few judgment calls: Turkey→Asia, Russia/RF→Europe,
Switzerland(`SWZ`)→Europe, Australia/null/`OT`→Other). Cached as `ai_proficiency_by_region`,
same 25h TTL / nightly-recompute pattern as the trend chart.

**Team Leaderboard:** per-department AI proficiency (name + bar + %), sourced from the same
`_compute_dept_snapshot()` cache (each department = % of members AI-proficient at `≥30`),
sorted best-first, shown top 6. Folded into the overview response as `team_leaderboard`.

**Proficiency by Vertical** and **Specialization Landscape** are **static placeholders**
(hardcoded in `nova_frontend/data.js` as `NOVA.managerStatic`, marked TODO). There is **no
business-vertical / specialization-track taxonomy** anywhere in Classmate/Fabric — real
`department_code` values are short internal codes (`dev`, `qa`, `hyd`, `mex`), unrelated to the
mockup's client-industry names — so these two charts wait for a real API.

### 6.9b Manager "Your Team" (direct reports, any manager)

Served by `GET /api/manager/your-team` (any manager; `_require_manager`). Layout: a top row of
a team radar, a badges donut, and a stacked right rail (Active learners + Courses completed this
week), then the people table. Returns:

- **people:** direct reports enriched via `_build_people_list()` — tier (live `tier_{uid}`
  overlay), AI proficiency, 90-day credits, last active, status (`at risk` if AI proficiency
  < 20, else `on track`), and **`streak_days`** (from `get_team_streaks()`, reading warm
  `streak_{uid}` caches / computing misses per-user). The people table shows a 🔥 streak pill
  next to the name when `streak_days > 0`.
- **radar:** team-averaged skill radar with two series (`get_team_skill_radar()` averages each
  axis's `this_month` / `last_month` across the reports, reusing the `classify_{uid}` cache).
  The frontend applies a visual floor shift (0 → the 25% ring) so an all-zero team doesn't
  collapse to the center, and passes the true (unshifted) values as per-axis % labels — matching
  the employee Skill Growth radar.
- **badges:** team badge summary (`get_team_badge_summary()` in `nova_db/badges.py`) — total,
  avg per person, this-month count, and a per-tier breakdown — rendered as a **hollow donut**
  (`DonutChart` in `charts.jsx`) with the total in the center and a color key listing each
  tier's count beside it.
- **active_this_week / courses_this_week:** direct-reports-scoped counts for the current week,
  via `_get_team_active_this_week(uids)` (3-source activity union) and
  `_get_team_courses_completed_this_week(uids)` (completed `vw_classmate_trainings` rows). Both
  reuse the same weekly window as the company metric, filtered to the manager's report uids;
  shown as the two rail cards (count + "% / across N direct reports"). `team_size` is the
  denominator. Cached with radar/badges under `your_team_v3_{mgr_id}` (25h TTL, SWR-served).

**People search** (`GET /api/manager/people/search`, fuzzy) is scoped by exec status: exec
managers search company-wide (Pradeep additionally recursive-org); everyone else searches only
their direct reports. Results carry the same enriched fields incl. `streak_days`.

### 6.10 Congrats

Peers can congratulate an accomplishment (`POST /api/congrats` with receiver + activity_id +
message). Stored idempotently per `(sender, receiver, activity_id)`. The employee dashboard
shows an all-time received count; the team view shows a 7-day team count.

---

## 7. Caching & refresh cycle

Everything expensive is precomputed into SQLite; endpoints read cache and use
**stale-while-revalidate** (return stale instantly, recompute in the background) so a request
never blocks on Fabric.

- **Startup:** init all SQLite tables, test Fabric, then fire background jobs — prewarm skill
  (`classify_*`) and streak caches, grade any unscored courses, compute company stats /
  retention / at-risk / quarterly AI trend / department snapshot / AI-proficiency-by-region
  snapshot, and refresh tier scores.
- **Nightly at 03:00 UTC** (`_run_nightly_refresh`, after the upstream ETL): **[on the 1st]
  award last month's badges →** force-refresh tier scores (current month) → prewarm skill &
  streak → recompute company stats/trend/dept snapshot/region snapshot → clear & rebuild
  per-manager caches.
- **Typical TTLs:** most derived `gpt_cache` entries 24–25h; `user_tier_scores` 24h;
  `dept_ai_baseline` ~60 days; `course_vertical_scores` and app-owned tables persist.

**Operational note:** if you change a scoring formula/threshold, purge the affected derived
caches (`tier_*`, `dept_snapshot`, `ai_proficiency_trend`, `company_*`, etc.) and let the
endpoints/nightly job recompute. Never purge `course_vertical_scores` casually (slow +
costs LLM calls to rebuild) or the app-owned `user_badges` / `congrats`.

---

## 8. Frontend data flow

1. `data.js` seeds `window.NOVA` (nulls, `TIERS`, and `managerStatic` — the static
   Vertical/Specialization placeholder data).
2. `api.js` `initNova()` runs on load: fetches `/api/me` (which includes `is_exec_manager`),
   determines role, then fetches the role's endpoints. Manager data is loaded by
   `loadManagerData(isExec)`: it always fetches `/api/manager/your-team` and fetches
   `/api/manager/overview` **only for exec managers** (avoids a needless 403). Mappers
   (`mapMe`, `mapDashboard`, `mapTeam`, `mapOverview`, `mapYourTeam`) reshape backend JSON into
   `NOVA.employee` / `NOVA.team` / `NOVA.manager` / `NOVA.accounts`. `NOVA.manager` holds
   `{isExec, overview, team, static}`. A `__novaDataReady` promise gates rendering; for role
   "both" a `nova-manager-ready` event re-renders when manager data lands.
3. `app.jsx` is the shell: tabs per role, account switching (employee↔manager for "both"),
   loading/sign-in/error gating. Manager tabs are `Overview` + `Your Team`, and `Overview` is
   dropped from the list entirely when `NOVA.accounts.isExecManager` is false. `ProfileMenu`
   holds the light/dark toggle (calls `applyTheme` + `saveColorMode` from `api.js`).
4. Views: **MyProgress** (tier card + badges, streak, skill radar, continue/recommended),
   **MyTeam** (highlights, accomplishments with congrats, team recommendations),
   **MgrOverview** (two-column: chart card with "Trend"/"By region" toggle + Proficiency-by-Vertical
   on the left; Active-learners hero + Specialization Landscape + Team Leaderboard on the
   right), **MgrYourTeam** (team radar + badges donut + active/courses-this-week rail, then the
   searchable people table with 🔥 streak pills).
5. `charts.jsx`: **RadarChart** (5-axis skill polygon; this-month solid, last-month dashed,
   optional teammate compare, optional per-axis `labelValues` %), **LineChart** (quarterly
   AI-proficient % filled line + % active dashed line + 80% target), **RegionProficiencyChart**
   (grouped bars per proficiency level — one bar per region at that region's own proficiency
   rate, with a dashed per-level goal line, a company-actual tick, and a hover tooltip), and
   **DonutChart** (hollow ring with a centered total and a color key). Chart neutrals use
   `--chart-grid`/`--chart-label`/`--tooltip-*` tokens so charts stay legible in dark mode;
   brand series colors are theme-independent. Ribbons/tier visuals in `icons.jsx`.
6. **Theming:** `styles.css` defines all neutral/surface/chart tokens under `:root` and a dark
   override under `:root[data-theme="dark"]` (brand accents stay identical). `<html data-theme>`
   is set pre-paint by an inline script in `index.html` (from `localStorage.nova_theme`) and
   reconciled by `initNova` from the account's `color_mode`. Buttons don't inherit page text
   color, so interactive chrome (e.g. `.profile-btn`) sets `color:var(--ink)` explicitly.

The frontend mapping is where raw fields become display fields — e.g. `tier.progress →
E.tierProgress`, `skills.this_month → radar`, `monthly_trend → overview.months + series`,
`proficiency_by_region → overview.proficiencyByRegion` (via `mapProficiencyByRegion`),
`team_leaderboard → overview.teamLeaderboard`, `your-team employees → team.people`
(with `streak_days → streak`), `active_this_week/courses_this_week → team.activeThisWeek /
team.coursesThisWeek`, `color_mode → account.colorMode`, badge counts → donut segments.

---

## 9. Key constants & thresholds (quick reference)

| Thing | Value |
|---|---|
| Skill normalization | `min(100, (raw/5000)^0.4 * 100)` |
| AI-proficient threshold | score ≥ **30** |
| Proficiency levels (cumulative) | professional ≥30 / specialist ≥45 / expert ≥55 / champion ≥65 |
| Proficiency level goals | professional 80% / specialist 50% / expert 35% / champion 20% |
| Region mapping | `core/geo.py` — Asia/NA/Europe/Other from `country_code` |
| Tier weights | credits .30 / skill .35 / consistency .20 / recency .15 |
| Monthly credit target | **100** credits → credits_score 100 |
| Tier percentile cutoffs | platinum 3 / diamond 10 / gold 20 / silver 40 / bronze 60 |
| Company at-risk | health `0.7*AI + 0.3*active < 0.20` |
| Retention | active in last 30 days ÷ headcount |
| Streak min | 1800 s/day setting (activity-based day detection) |
| Completed status | `4052` (course), `2` (cert/self-study); in-progress `4035` |
| Dev user | Pradeep Menon `5575`; exec devs `16467/16465/16470` |
| Exec managers (Overview gate) | `EXEC_USER_IDS` ∩ managers → Pradeep `5575` only today |
| LLM | Azure OpenAI `gpt-4o-mini` |
| Nightly refresh | 03:00 UTC |

---

## 10. Invariants & gotchas

1. **Always dedupe `dim_classmate_employee_profile`** (latest row per user) or you get
   massive SCD fan-out.
2. **One tier formula, two compute sites** (the monthly refresh and the badge job) must stay
   identical — they share one helper so the ranking population and the cached tier dicts can't
   drift.
3. **Tiers/skill are pure cache reads everywhere** — never recompute per view; that's what
   keeps employee and manager numbers consistent.
4. **Don't cache results from a failed Fabric fetch** (the `queries_ok` guard for skill; the
   population helper aborts if the base query returns nothing) — otherwise all-zero/`starter`
   values poison the cache until TTL.
5. **Monthly reset is intentional** — early each month tiers are low and climb; the AI-trend
   chart deliberately hides the in-progress quarter.
6. **Skill/proficiency are long-term; only tier is monthly.** Recalibrating the tier's monthly
   divisors (`monthly_credit_target`, days-in-month) changes tier distribution but not skill
   or proficiency numbers.
7. **`FABRIC_DRIVER`** default is a macOS path; on Linux/Azure set it to the ODBC driver name
   and ensure msodbcsql18 is installed, or Fabric connections fail.

---

## 11. Recent changes (this handoff period)

- **Monthly tiers + badges** (new): converted the tier from all-time to monthly
  (credits/recency/consistency windowed to the calendar month; skill stays all-time); added
  the month-end badge-award job (idempotent, excludes starter) wired into the nightly refresh;
  built the badge display (best→worst columns, stacked repeats, "+N" overflow).
- **AI-proficiency recalibration & unification:** lowered the proficient threshold to 30 and
  unified all three proficiency computations (Teams, direct-reports count, Overview trend)
  onto the same `^0.4` curve + single config threshold, so Overview and Teams agree.
- **Teams trend fix:** replaced the poison-prone per-run baseline with a period-stable
  `dept_ai_baseline` (30-day window, degenerate-guarded) so the "trend" is an honest delta.
- **Trend chart fix:** excluded the in-progress quarter so the line no longer dips on partial
  month/quarter data.
- Earlier employee/manager dashboard fixes (real congrats, team deltas, retention, company
  KPIs, null-guards) — see git history on the parent repo.
