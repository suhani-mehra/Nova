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
- **Manager view ("Overview" / "Teams" / "People")** — company KPIs (headcount, active
  learners, % AI-proficient, retention, at-risk count), an AI-proficiency trend chart by
  quarter, per-department proficiency, and a searchable people list with each person's tier
  and proficiency.

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
  display_name). This is a **slowly-changing dimension with many rows per person**, so every
  read must deduplicate to the latest row per `user_id` (via a `ROW_NUMBER() … ORDER BY
  modified_on DESC` CTE — the `_DEDUP_CTE`). Forgetting this fans out ~73k rows for ~13k
  people.
- `dim_classmate_user` — identity (email/`aduser_name`, names).
- `dim_classmate_second_level_category` / `dim_classmate_certificate` — the course/cert
  catalog (source of items to grade).

Common filters: `is_active=1 AND is_deleted=0 AND etl_isactive=1`.

---

## 4. Local SQLite cache (`nova_local.db`)

Five tables. Three are **derived caches** (safe to purge/rebuild); two are **app-owned data**
(must persist).

| Table | Kind | Contents |
|---|---|---|
| `gpt_cache` | derived | Generic key→JSON cache with per-row expiry. Holds skill radars (`classify_{uid}`), tier dicts (`tier_{uid}`), company stats, trends, team lists, streaks, etc. |
| `course_vertical_scores` | derived (slow to rebuild) | The LLM's per-course 5-vertical grades. ~24.5k rows. |
| `user_tier_scores` | derived | The percentile-ranking population: `{user_id → composite tier_score}` for the current month. |
| `user_badges` | **app-owned** | Monthly badges: `(user_id, tier, month 'YYYY-MM', awarded_at)`, `UNIQUE(user_id, month)`. |
| `congrats` | **app-owned** | Peer congratulations: `(sender, receiver, activity_id, message, created_at)`. |

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
- **Dev impersonation:** exec dev users (IDs `16467`, `16465`, `16470` = Niva Shah, Eric
  Verdes, Suhani Mehra) can pass `X-Nova-Dev-User` (sign in as any user) or
  `X-Nova-Impersonate` (view as another user). The frontend stores these in sessionStorage.
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

### 6.9 Manager KPIs (Overview)

- **Total team / headcount:** count of active employees company-wide.
- **Active this week:** employees with any activity in the current week (from streak cache).
- **% / # AI-proficient:** from the latest completed quarter of the AI-proficiency trend.
- **Avg credits this quarter:** mean of per-user completed credits over the last 90 days.
- **Retention rate:** share of employees active (any learning credit) in the last 30 days;
  trend = current 30-day window vs the prior 30-day window.
- **At-risk count (company KPI):** a health score `0.7*(AI/100) + 0.3*(active_this_week?1:0)`;
  a person is **at risk if health < 0.20**. (Note: the *employee/team* at-risk list in the
  team service uses a different rule — inactive ≥ 14 days **and** credits below the team
  average — since it answers a different, team-local question.)

**AI-proficiency trend chart:** builds 6 completed quarters (plus a hidden warm-up quarter
for context). Per quarter, each employee's cumulative AI raw (up to that quarter's end) is
normalized with the same `^0.4` curve and compared to the `≥30` threshold; the line is the
% of employees proficient. A second dashed line shows **% active learners** per quarter. The
**current, in-progress quarter is excluded** (the chart is "measured at quarter end") so the
line doesn't dip on partial data. Target line drawn at 80%.

**Teams (per-department):** for each department, % of members who are AI-proficient (`≥30`).
The **trend** compares the current % against a **period-stable baseline** (`dept_ai_baseline`,
refreshed ~every 30 days, never seeded from a degenerate/cold snapshot) so it shows an honest
month-over-month delta rather than a spurious jump. Status badge: ≥70% "On track", ≥50%
"Needs focus", else "Falling behind".

**People:** manager's direct reports with tier (live `tier_{uid}` overlay), AI proficiency,
credits (90-day), last active, and status (`at risk` if AI proficiency < 20, else `on
track`). Search supports direct-reports / company-wide / recursive-org scopes (fuzzy match)
with the same enriched fields.

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
  retention / at-risk / quarterly AI trend / department snapshot, and refresh tier scores.
- **Nightly at 03:00 UTC** (`_run_nightly_refresh`, after the upstream ETL): **[on the 1st]
  award last month's badges →** force-refresh tier scores (current month) → prewarm skill &
  streak → recompute company stats/trend/dept snapshot → clear & rebuild per-manager caches.
- **Typical TTLs:** most derived `gpt_cache` entries 24–25h; `user_tier_scores` 24h;
  `dept_ai_baseline` ~60 days; `course_vertical_scores` and app-owned tables persist.

**Operational note:** if you change a scoring formula/threshold, purge the affected derived
caches (`tier_*`, `dept_snapshot`, `ai_proficiency_trend`, `company_*`, etc.) and let the
endpoints/nightly job recompute. Never purge `course_vertical_scores` casually (slow +
costs LLM calls to rebuild) or the app-owned `user_badges` / `congrats`.

---

## 8. Frontend data flow

1. `data.js` seeds an empty `window.NOVA` shape (and fallback demo values).
2. `api.js` `initNova()` runs on load: fetches `/api/me`, determines role, then fetches the
   role's endpoints (employee dashboard+team, and/or manager overview+teams+people). Mapper
   functions (`mapMe`, `mapDashboard`, `mapTeam`, `mapManager`) reshape backend JSON into the
   `NOVA.employee` / `NOVA.team` / `NOVA.manager` / `NOVA.accounts` structures. A
   `__novaDataReady` promise gates rendering; for role "both" a `nova-manager-ready` event
   re-renders when manager data lands.
3. `app.jsx` is the shell: tabs per role, account switching (employee↔manager for "both"),
   loading/sign-in/error gating.
4. Views: **MyProgress** (tier card + badges, streak, skill radar, continue/recommended),
   **MyTeam** (highlights, accomplishments with congrats, team recommendations),
   **MgrOverview** (KPI cards + trend line chart), **MgrTeams** (per-dept proficiency bars),
   **MgrPeople** (searchable people table).
5. `charts.jsx`: **RadarChart** (5-axis skill polygon; this-month solid, last-month dashed,
   optional teammate compare) and **LineChart** (quarterly AI-proficient % filled line + %
   active dashed line + 80% target). Ribbons/tier visuals in `icons.jsx`.

The frontend mapping is where raw fields become display fields — e.g. `tier.progress →
E.tierProgress`, `skills.this_month → radar`, `monthly_trend → months + series`,
`departments[].ai_proficient_pct → team.prof`, badge rows → grouped tier columns.

---

## 9. Key constants & thresholds (quick reference)

| Thing | Value |
|---|---|
| Skill normalization | `min(100, (raw/5000)^0.4 * 100)` |
| AI-proficient threshold | score ≥ **30** |
| Tier weights | credits .30 / skill .35 / consistency .20 / recency .15 |
| Monthly credit target | **100** credits → credits_score 100 |
| Tier percentile cutoffs | platinum 3 / diamond 10 / gold 20 / silver 40 / bronze 60 |
| Company at-risk | health `0.7*AI + 0.3*active < 0.20` |
| Retention | active in last 30 days ÷ headcount |
| Streak min | 1800 s/day setting (activity-based day detection) |
| Completed status | `4052` (course), `2` (cert/self-study); in-progress `4035` |
| Dev user | Pradeep Menon `5575`; exec devs `16467/16465/16470` |
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
