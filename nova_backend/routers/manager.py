"""
routers/manager.py
Manager-only endpoints: /api/manager/overview, /api/manager/teams,
/api/manager/people, /api/manager/people/search.
"""

import asyncio
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.auth import CurrentUser, get_current_user
from core.database import query as _query
from core.config import settings
from core.queries import get_direct_reports, get_manager_monthly_trend
from services.team_service import get_at_risk_employees

logger = logging.getLogger(__name__)
router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=8)

_trend_computing = False  # guard against concurrent recomputes

# ── Exec access sets ──────────────────────────────────────────────────────────

EXEC_USER_IDS: set[int] = {5575}
EXEC_USER_NAMES = [
    "suhani mehra",
    "niva nimesh shah",
    "eric verdes",
]
RECURSIVE_USER_IDS: set[int] = {5575}


def _init_exec_users():
    global EXEC_USER_IDS
    from core.queries import _DEDUP_CTE
    try:
        placeholders = ",".join("?" * len(EXEC_USER_NAMES))
        rows = _query(
            _DEDUP_CTE + f"""
            SELECT user_id
            FROM latest_profiles
            WHERE rn = 1
              AND LOWER(TRIM(display_name))
                  IN ({placeholders})
            """,
            tuple(EXEC_USER_NAMES),
        )
        for r in rows:
            EXEC_USER_IDS.add(r["user_id"])
        logger.info("Exec user_ids resolved: %s", EXEC_USER_IDS)
    except Exception as exc:
        logger.warning("Could not resolve exec user_ids: %s", exc)


# ── Placeholder data ──────────────────────────────────────────────────────────

_PLACEHOLDER_OVERVIEW = {
    "kpis": {
        "total_team":               8,
        "active_this_week":         5,
        "ai_proficient_count":      3,
        "ai_proficient_pct":        37.5,
        "avg_credits_this_quarter": 24.2,
    },
    "monthly_trend": [
        {"month": "Jan", "credits": 180.0, "completions": 12},
        {"month": "Feb", "credits": 210.0, "completions": 14},
        {"month": "Mar", "credits": 195.0, "completions": 13},
        {"month": "Apr", "credits": 230.0, "completions": 16},
        {"month": "May", "credits": 220.0, "completions": 15},
        {"month": "Jun", "credits": 250.0, "completions": 18},
    ],
    "at_risk": [],
}

_PLACEHOLDER_TEAMS = {
    "departments": [
        {
            "name":              "Engineering",
            "headcount":         5,
            "avg_credits":       28.4,
            "ai_proficient_pct": 40.0,
            "top_course":        "Python for Data Science",
            "tier_distribution": {
                "platinum": 0, "diamond": 1, "gold": 2,
                "silver": 1, "bronze": 1, "starter": 0,
            },
        }
    ]
}

_PLACEHOLDER_PEOPLE = {
    "employees": [
        {
            "user_id":              1,
            "name":                 "Alice Kumar",
            "department":           "Engineering",
            "tier":                 "gold",
            "credits_this_quarter": 32.0,
            "streak":               0,
            "ai_proficiency":       72.0,
            "status":               "thriving",
            "last_active":          "2025-06-14",
        }
    ]
}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


def _require_manager(user: CurrentUser):
    if user.classmate_user_id is not None and user.role not in ("manager", "both"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager role required",
        )


def _active_this_week_count(uids: list[int]) -> int:
    if not uids:
        return 0
    placeholders = ",".join("?" * len(uids))
    rows = _query(
        f"""
        SELECT COUNT(DISTINCT user_id) AS cnt
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id IN ({placeholders})
          AND  is_deleted  = 0
          AND  credit_date >= DATEADD(day, -7, GETDATE())
        """,
        tuple(uids),
    )
    return int(rows[0]["cnt"] or 0) if rows else 0


def _compute_quarterly_ai_proficiency() -> list[dict]:
    """
    Computes 8 quarters of AI proficiency % across all active employees.
    Returns [{"month": "Q3 '24", "credits": 12.5}, ...] — cached 24 h.
    'credits' carries the % value so api.js can map it without changes.
    """
    global _trend_computing
    import math
    from datetime import date
    from nova_db.gpt_cache import get_cache, set_cache
    from nova_db.course_scores import get_scores_for_items
    from services.skill_service import MASTERY_THRESHOLD

    CACHE_KEY = "ai_proficiency_trend"
    cached = get_cache(CACHE_KEY)
    if cached:
        _trend_computing = False
        return cached["result"]

    _trend_computing = True
    logger.info("ai_proficiency_trend: computing quarterly trend (company-wide)")

    # All active employees — denominator for every quarter
    emp_rows = _query("""
        SELECT DISTINCT user_id
        FROM classmate.dim_classmate_employee_profile
        WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0
          AND user_id IS NOT NULL
    """)
    all_uids = {r["user_id"] for r in emp_rows if r["user_id"]}
    total = len(all_uids)
    if not total:
        return []

    # Fetch all completed trainings (all time, all users)
    try:
        training_rows = _query("""
            SELECT user_id, second_level_category_id AS item_id,
                   'course' AS item_type, completed_on
            FROM classmate.vw_classmate_trainings
            WHERE status=4052 AND user_id IS NOT NULL
              AND second_level_category_id IS NOT NULL AND completed_on IS NOT NULL
        """)
    except Exception as exc:
        logger.warning("ai_proficiency_trend: trainings query failed: %s", exc)
        training_rows = []

    try:
        cert_rows = _query("""
            SELECT fc.user_id, fc.certificate_id AS item_id,
                   'cert' AS item_type, fc.completion_date AS completed_on
            FROM classmate.fact_classmate_certification fc
            WHERE fc.status=2 AND fc.is_active=1 AND fc.is_deleted=0
              AND fc.user_id IS NOT NULL AND fc.certificate_id IS NOT NULL
              AND fc.completion_date IS NOT NULL
        """)
    except Exception as exc:
        logger.warning("ai_proficiency_trend: certs query failed: %s", exc)
        cert_rows = []

    # LC — one row per (user, distinct topic), earliest completion date
    try:
        lc_rows = _query("""
            SELECT user_id, topic AS name, MIN(credit_date) AS completed_on
            FROM classmate.fact_classmate_learning_credit
            WHERE is_deleted=0
              AND topic IS NOT NULL AND topic != ''
              AND (self_study_id IS NOT NULL
                   OR session_id IS NOT NULL
                   OR recorded_training_id IS NOT NULL)
              AND user_id IS NOT NULL AND credit_date IS NOT NULL
            GROUP BY user_id, topic
        """)
    except Exception as exc:
        logger.warning("ai_proficiency_trend: LC query failed: %s", exc)
        lc_rows = []

    import zlib as _zlib

    def _tid(topic: str) -> int:
        return _zlib.crc32(topic.encode("utf-8", errors="replace")) & 0x7FFFFFFF

    # Collect all (uid, pair, date) events
    all_events: list[tuple] = []
    lookup_pairs: set[tuple] = set()

    for r in training_rows:
        uid = r["user_id"]
        if uid not in all_uids:
            continue
        co = r["completed_on"]
        co_date = co.date() if hasattr(co, "date") else co
        pair = ("course", int(r["item_id"]))
        all_events.append((uid, pair, co_date))
        lookup_pairs.add(pair)

    for r in cert_rows:
        uid = r["user_id"]
        if uid not in all_uids:
            continue
        co = r["completed_on"]
        co_date = co.date() if hasattr(co, "date") else co
        pair = ("cert", int(r["item_id"]))
        all_events.append((uid, pair, co_date))
        lookup_pairs.add(pair)

    for r in lc_rows:
        uid  = r["user_id"]
        name = r.get("name") or ""
        co   = r["completed_on"]
        if uid not in all_uids or not name:
            continue
        co_date = co.date() if hasattr(co, "date") else co
        pair = ("lc", _tid(name))
        all_events.append((uid, pair, co_date))
        lookup_pairs.add(pair)

    # Batch-load AI scores from SQLite
    score_map = get_scores_for_items(list(lookup_pairs))

    # Build per-user AI contributions: list of (date, ai_value)
    # Deduplicate LC topics per user (same topic at different dates → earliest date only)
    user_ai: dict[int, list[tuple]] = {uid: [] for uid in all_uids}
    seen_lc_per_user: dict[int, set] = {uid: set() for uid in all_uids}
    for uid, pair, co_date in all_events:
        if pair[0] == "lc":
            if pair[1] in seen_lc_per_user[uid]:
                continue
            seen_lc_per_user[uid].add(pair[1])
        sc = score_map.get(pair)
        if sc is None:
            continue
        ai_val = float(sc.get("AI", 0))
        if ai_val > 0:
            user_ai[uid].append((co_date, ai_val))

    # Generate quarter end dates (8 quarters back from today)
    today = date.today()
    q_end_dates = [
        (1, 3, 31), (2, 6, 30), (3, 9, 30), (4, 12, 31),
    ]
    q_idx = (today.month - 1) // 3  # 0-3 = current quarter
    yr = today.year
    quarters: list[tuple[str, date]] = []
    for _ in range(6):
        _, m, d = q_end_dates[q_idx]
        q_end = date(yr, m, d)
        if q_end > today:
            q_end = today
        label = f"Q{q_idx + 1} '{str(yr)[2:]}"
        quarters.append((label, q_end))
        q_idx -= 1
        if q_idx < 0:
            q_idx = 3
            yr -= 1
    quarters.reverse()

    # For each quarter cutoff, count AI-proficient employees
    result = []
    for label, cutoff in quarters:
        proficient = sum(
            1 for uid in all_uids
            if min(100.0, math.sqrt(
                sum(v for d, v in user_ai[uid] if d <= cutoff) / MASTERY_THRESHOLD
            ) * 100) >= 60.0
        )
        pct = round(proficient / total * 100, 1)
        result.append({"month": label, "credits": pct})

    logger.info(
        "ai_proficiency_trend: complete — %d employees, latest quarter: %s%%",
        total, result[-1]["credits"] if result else "n/a",
    )
    set_cache(CACHE_KEY, result, "computed", ttl_hours=24)
    _trend_computing = False
    return result


def _default_quarterly_trend() -> list[dict]:
    """Placeholder quarters with 0 until background job completes."""
    from datetime import date
    today = date.today()
    q_idx = (today.month - 1) // 3
    yr = today.year
    q_end_months = [3, 6, 9, 12]
    quarters = []
    for _ in range(6):
        label = f"Q{q_idx + 1} '{str(yr)[2:]}"
        quarters.append({"month": label, "credits": 0.0})
        q_idx -= 1
        if q_idx < 0:
            q_idx = 3
            yr -= 1
    quarters.reverse()
    return quarters


def _avg_credits_this_quarter(uids: list[int]) -> float:
    if not uids:
        return 0.0
    placeholders = ",".join("?" * len(uids))
    rows = _query(
        f"""
        SELECT AVG(s.credits) AS avg_c
        FROM (
            SELECT user_id, SUM(learning_credits) AS credits
            FROM   classmate.vw_classmate_trainings
            WHERE  user_id IN ({placeholders})
              AND  status = 4052
              AND  completed_on >= DATEADD(day, -90, GETDATE())
            GROUP BY user_id
        ) s
        """,
        tuple(uids),
    )
    return round(float(rows[0]["avg_c"] or 0), 1) if rows else 0.0


# ── Search helpers ────────────────────────────────────────────────────────────

def _fuzzy_filter(rows: list[dict], q: str) -> list[dict]:
    if not q or not q.strip():
        return rows
    q = q.strip().lower()
    tokens = q.split()

    def _lev(a: str, b: str) -> int:
        if a == b: return 0
        if not a: return len(b)
        if not b: return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                curr.append(min(
                    prev[j] + (0 if ca == cb else 1),
                    curr[j] + 1,
                    prev[j + 1] + 1))
            prev = curr
        return prev[-1]

    def _match_pos(name: str, query: str) -> int:
        """
        Returns a sort key (lower = better rank) for how well the query
        matches the name:
          0 — name starts with the query (e.g. "pra" → "Pradeep")
          1 — a word in the name starts with the query (e.g. "men" → "Pradeep Menon")
          2 — query appears somewhere else in the name
        """
        if name.startswith(query):
            return 0
        if any(w.startswith(query) for w in name.split()):
            return 1
        return 2

    exact, token_match, fuzzy_match = [], [], []
    for r in rows:
        name = (r.get("name") or "").lower()
        if not name:
            continue
        if q in name:
            exact.append((_match_pos(name, q), r))
        elif all(t in name for t in tokens):
            # For multi-token queries rank by how early the first token appears
            pos = min(_match_pos(name, t) for t in tokens)
            token_match.append((pos, r))
        else:
            name_words = name.split()
            if any(
                _lev(t, w) <= 2
                for t in tokens
                for w in name_words
                if abs(len(t) - len(w)) <= 2
            ):
                fuzzy_match.append(r)

    exact.sort(key=lambda x: x[0])
    token_match.sort(key=lambda x: x[0])
    return ([r for _, r in exact] + [r for _, r in token_match] + fuzzy_match)[:50]


def _search_direct_reports(mgr_id: int, q: str) -> list[dict]:
    reports = get_direct_reports(None, mgr_id)
    return _fuzzy_filter(reports, q)


def _search_recursive(mgr_id: int, q: str) -> list[dict]:
    rows = _query(
        """
        WITH latest_profiles AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id
                    ORDER BY modified_on DESC
                ) AS rn
            FROM classmate.dim_classmate_employee_profile
            WHERE etl_isactive = 1
              AND is_active    = 1
              AND is_deleted   = 0
        ),
        deduped AS (
            SELECT * FROM latest_profiles WHERE rn = 1
        ),
        org_tree AS (
            SELECT user_id,
                   LOWER(TRIM(display_name))     AS name,
                   LOWER(TRIM(department_code))  AS department,
                   LOWER(TRIM(designation_code)) AS designation,
                   manager,
                   1 AS depth
            FROM deduped
            WHERE manager = ?
            UNION ALL
            SELECT d.user_id,
                   LOWER(TRIM(d.display_name)),
                   LOWER(TRIM(d.department_code)),
                   LOWER(TRIM(d.designation_code)),
                   d.manager,
                   t.depth + 1
            FROM deduped d
            JOIN org_tree t ON d.manager = t.user_id
            WHERE t.depth < 10
        )
        SELECT DISTINCT user_id, name, department, designation
        FROM org_tree
        """,
        (mgr_id,),
    )
    for r in rows:
        for f in ("name", "department", "designation"):
            if r.get(f):
                r[f] = r[f].title()
    return _fuzzy_filter(rows, q)


def _search_company_wide(q: str) -> list[dict]:
    from core.queries import _DEDUP_CTE
    rows = _query(
        _DEDUP_CTE + """
        SELECT ep.user_id,
            LOWER(TRIM(ep.display_name))     AS name,
            LOWER(TRIM(ep.department_code))  AS department,
            LOWER(TRIM(ep.designation_code)) AS designation
        FROM latest_profiles ep
        WHERE ep.rn = 1
        ORDER BY ep.display_name
        """,
    )
    for r in rows:
        for f in ("name", "department", "designation"):
            if r.get(f):
                r[f] = r[f].title()
    return _fuzzy_filter(rows, q)


def _enrich_search_results(uids: list[int], rows: list[dict]) -> list[dict]:
    if not uids:
        return []
    ph = ",".join("?" * len(uids))
    uid_to_row = {r["user_id"]: r for r in rows}

    uid_credits: dict[int, float] = {}
    try:
        cr = _query(
            f"""SELECT user_id, SUM(learning_credits) AS credits
                FROM classmate.vw_classmate_trainings
                WHERE user_id IN ({ph})
                  AND status=4052
                  AND completed_on >= DATEADD(day,-90,GETDATE())
                GROUP BY user_id""",
            tuple(uids),
        )
        uid_credits = {r["user_id"]: float(r["credits"] or 0) for r in cr}
    except Exception:
        pass

    uid_last: dict[int, str] = {}
    try:
        lr = _query(
            f"""SELECT user_id, MAX(credit_date) AS last
                FROM classmate.fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0
                GROUP BY user_id""",
            tuple(uids),
        )
        for r in lr:
            d = r["last"]
            if d:
                uid_last[r["user_id"]] = str(d.date() if hasattr(d, "date") else d)
    except Exception:
        pass

    uid_ai: dict[int, float] = {}
    try:
        from services.skill_service import get_team_skill_scores
        team_norm = get_team_skill_scores(uids)
        uid_ai = {uid: round(team_norm.get(uid, {}).get("AI", 0.0), 1) for uid in uids}
    except Exception as exc:
        logger.warning("_enrich_search_results: skill scores failed: %s", exc)

    result = []
    for uid in uids:
        r = uid_to_row.get(uid, {})
        result.append({
            "user_id":              uid,
            "name":                 r.get("name", ""),
            "department":           r.get("department", "Unknown"),
            "designation":          r.get("designation", ""),
            "tier":                 "—",
            "credits_this_quarter": round(uid_credits.get(uid, 0.0), 1),
            "streak":               0,
            "ai_proficiency":       uid_ai.get(uid, 0.0),
            "status":               "—",
            "last_active":          uid_last.get(uid, "never"),
        })
    return result


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/manager/overview")
async def manager_overview(user: CurrentUser = Depends(get_current_user)):
    _require_manager(user)
    if user.classmate_user_id is None:
        return _PLACEHOLDER_OVERVIEW

    global _trend_computing
    mgr_id = user.classmate_user_id
    try:
        reports, at_risk = await asyncio.gather(
            _run(get_direct_reports, None, mgr_id),
            _run(get_at_risk_employees, mgr_id),
        )
        from nova_db.gpt_cache import get_cache
        cached_trend = get_cache("ai_proficiency_trend")
        if cached_trend:
            monthly_trend = cached_trend["result"]
        else:
            monthly_trend = _default_quarterly_trend()
            if not _trend_computing:
                _trend_computing = True
                loop = asyncio.get_event_loop()
                loop.run_in_executor(_executor, _compute_quarterly_ai_proficiency)
        uids = [r["user_id"] for r in reports]

        # KPI: company-wide AI proficiency % from latest cached quarter
        if cached_trend and cached_trend["result"]:
            latest_q = cached_trend["result"][-1]
            ai_prof_pct = latest_q["credits"]
            # Estimate count from total active employees proportionally
            emp_count_rows = _query("""
                SELECT COUNT(DISTINCT user_id) AS n
                FROM classmate.dim_classmate_employee_profile
                WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
            """)
            total_emp = int(emp_count_rows[0]["n"] or 0) if emp_count_rows else 0
            ai_prof_count = round(total_emp * ai_prof_pct / 100)
        else:
            # Fallback to direct reports until background job populates cache
            from services.skill_service import get_team_skill_scores
            team_norm = await _run(get_team_skill_scores, uids)
            threshold = settings.ai_proficiency_min_score
            ai_prof_count = sum(
                1 for uid in uids
                if team_norm.get(uid, {}).get("AI", 0) >= threshold
            )
            ai_prof_pct = round(ai_prof_count / len(uids) * 100, 1) if uids else 0.0
            total_emp = len(uids)
        ai_prof = {
            "count": ai_prof_count,
            "pct":   ai_prof_pct,
            "total": total_emp,
        }

        active_week = await _run(_active_this_week_count, uids)
        avg_q_credits = await _run(_avg_credits_this_quarter, uids)
    except Exception as exc:
        logger.warning("Fabric unavailable for manager overview uid=%s: %s", mgr_id, exc)
        return _PLACEHOLDER_OVERVIEW

    return {
        "kpis": {
            "total_team":               ai_prof["total"],
            "active_this_week":         active_week,
            "ai_proficient_count":      ai_prof["count"],
            "ai_proficient_pct":        ai_prof["pct"],
            "avg_credits_this_quarter": avg_q_credits,
        },
        "monthly_trend": monthly_trend,
        "at_risk":       at_risk,
    }


@router.get("/manager/teams")
async def manager_teams(user: CurrentUser = Depends(get_current_user)):
    _require_manager(user)
    if user.classmate_user_id is None:
        return _PLACEHOLDER_TEAMS

    mgr_id = user.classmate_user_id
    try:
        reports = await _run(get_direct_reports, None, mgr_id)
        if not reports:
            return {"departments": []}

        uids = [r["user_id"] for r in reports]
        placeholders = ",".join("?" * len(uids))

        dept_members: dict[str, list[int]] = defaultdict(list)
        for r in reports:
            dept = r["department"] or "Unknown"
            dept_members[dept].append(r["user_id"])

        credit_rows, top_course_rows = await asyncio.gather(
            _run(_query,
                f"""
                SELECT user_id, SUM(learning_credits) AS credits
                FROM   classmate.vw_classmate_trainings
                WHERE  user_id IN ({placeholders})
                  AND  status = 4052
                  AND  completed_on >= DATEADD(day, -90, GETDATE())
                GROUP BY user_id
                """,
                tuple(uids),
            ),
            _run(_query,
                f"""
                WITH latest_profiles AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY user_id ORDER BY modified_on DESC
                    ) AS rn
                    FROM classmate.dim_classmate_employee_profile
                    WHERE etl_isactive = 1 AND is_active = 1 AND is_deleted = 0
                )
                SELECT LOWER(TRIM(ep.department_code)) AS department,
                       vt.course_name, COUNT(*) AS cnt
                FROM   classmate.vw_classmate_trainings vt
                JOIN   latest_profiles ep ON ep.user_id = vt.user_id
                WHERE  ep.rn = 1
                  AND  vt.user_id IN ({placeholders})
                  AND  vt.status = 4052
                GROUP BY LOWER(TRIM(ep.department_code)), vt.course_name
                ORDER BY cnt DESC
                """,
                tuple(uids),
            ),
        )
        uid_credits = {r["user_id"]: float(r["credits"] or 0) for r in credit_rows}

        dept_top_course: dict[str, str] = {}
        seen_depts: set[str] = set()
        for r in top_course_rows:
            d = (r["department"] or "unknown").title()
            if d not in seen_depts:
                dept_top_course[d] = r["course_name"]
                seen_depts.add(d)

        tier_keys = ["platinum", "diamond", "gold", "silver", "bronze", "starter"]
        departments = []
        for dept, dept_uids in dept_members.items():
            dept_creds = [uid_credits.get(uid, 0.0) for uid in dept_uids]
            avg_c = round(sum(dept_creds) / len(dept_creds), 1) if dept_creds else 0.0
            departments.append({
                "name":              dept,
                "headcount":         len(dept_uids),
                "avg_credits":       avg_c,
                "ai_proficient_pct": 0.0,
                "top_course":        dept_top_course.get(dept, "N/A"),
                "tier_distribution": {k: 0 for k in tier_keys},
            })

    except Exception as exc:
        logger.warning("Fabric unavailable for manager teams uid=%s: %s", mgr_id, exc)
        return _PLACEHOLDER_TEAMS

    return {"departments": departments}


def _build_people_list(mgr_id: int, filter_val: str) -> list[dict]:
    from datetime import date
    from services.skill_service import get_team_skill_scores, AXES
    from services.tier_service import calculate_tier

    reports = get_direct_reports(None, mgr_id)
    if not reports:
        return []

    uids = [r["user_id"] for r in reports]
    uid_to_report = {r["user_id"]: r for r in reports}
    placeholders = ",".join("?" * len(uids))

    credit_rows = _query(
        f"""
        SELECT user_id, SUM(learning_credits) AS credits
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id IN ({placeholders})
          AND  status = 4052
          AND  completed_on >= DATEADD(day, -90, GETDATE())
        GROUP BY user_id
        """,
        tuple(uids),
    )
    uid_credits = {r["user_id"]: float(r["credits"] or 0) for r in credit_rows}
    avg_credits = sum(uid_credits.values()) / len(uids) if uids else 0.0

    uid_last_active: dict = {}
    try:
        last_rows = _query(
            f"""
            SELECT user_id, MAX(credit_date) AS last_date
            FROM   classmate.fact_classmate_learning_credit
            WHERE  user_id IN ({placeholders})
              AND  is_deleted = 0
            GROUP BY user_id
            """,
            tuple(uids),
        )
        uid_last_active = {r["user_id"]: r["last_date"] for r in last_rows}
    except Exception:
        pass

    # Team skill scores (one batch GPT call)
    try:
        team_norm = get_team_skill_scores(uids)
    except Exception as exc:
        logger.warning("get_team_skill_scores failed: %s", exc)
        team_norm = {}

    ai_idx = AXES.index("AI")

    today = date.today()
    employees = []
    for uid in uids:
        r = uid_to_report[uid]
        last = uid_last_active.get(uid)
        if last:
            last_date = last.date() if hasattr(last, "date") else last
            days_inactive = (today - last_date).days
            last_active_str = str(last_date)
        else:
            days_inactive = 999
            last_active_str = "never"

        emp_credits = uid_credits.get(uid, 0.0)
        active_7 = days_inactive <= 7

        if active_7 and emp_credits >= avg_credits:
            emp_status = "thriving"
        elif days_inactive >= 14 or emp_credits < avg_credits * 0.5:
            emp_status = "at_risk"
        else:
            emp_status = "on_track"

        if filter_val == "thriving" and emp_status != "thriving":
            continue
        if filter_val == "at_risk" and emp_status != "at_risk":
            continue

        ai_proficiency = round(team_norm.get(uid, {}).get("AI", 0.0), 1)
        scored_by = team_norm.get(uid, {}).get("_scored_by", "keywords")

        try:
            tier_data = calculate_tier(uid)
            emp_tier = tier_data["current_tier"]
        except Exception:
            all_creds = list(uid_credits.values())
            max_cred = max(all_creds) if all_creds else 0.0
            def _team_tier(credits: float) -> str:
                if max_cred == 0:
                    return "starter"
                pct = credits / max_cred * 100
                if pct >= 90: return "platinum"
                if pct >= 70: return "diamond"
                if pct >= 50: return "gold"
                if pct >= 30: return "silver"
                if pct >= 10: return "bronze"
                return "starter"
            emp_tier = _team_tier(emp_credits)

        employees.append({
            "user_id":              uid,
            "name":                 r["name"],
            "department":           r["department"] or "Unknown",
            "tier":                 emp_tier,
            "credits_this_quarter": round(emp_credits, 1),
            "streak":               0,
            "ai_proficiency":       ai_proficiency,
            "status":               emp_status,
            "last_active":          last_active_str,
            "scored_by":            scored_by,
        })

    return employees


@router.get("/manager/people")
async def manager_people(
    filter: str = Query("all", pattern="^(all|thriving|at_risk)$"),
    user: CurrentUser = Depends(get_current_user),
):
    _require_manager(user)
    if user.classmate_user_id is None:
        return _PLACEHOLDER_PEOPLE

    mgr_id = user.classmate_user_id
    try:
        employees = await _run(_build_people_list, mgr_id, filter)
    except Exception as exc:
        logger.warning("Fabric unavailable for manager people uid=%s: %s", mgr_id, exc)
        return _PLACEHOLDER_PEOPLE

    return {"employees": employees}


@router.get("/manager/people/search")
async def manager_people_search(
    q: str = Query("", min_length=0, max_length=100),
    user: CurrentUser = Depends(get_current_user),
):
    _require_manager(user)
    if not q or not q.strip():
        return {"employees": [], "search_scope": "none"}

    uid = user.classmate_user_id
    q_lower = q.strip().lower()

    if uid is None:
        return {"employees": [], "search_scope": "dev"}

    try:
        if uid in EXEC_USER_IDS and uid in RECURSIVE_USER_IDS:
            try:
                rows = await _run(_search_recursive, uid, q_lower)
                scope = "recursive"
            except Exception:
                rows = await _run(_search_company_wide, q_lower)
                scope = "company"
        elif uid in EXEC_USER_IDS:
            rows = await _run(_search_company_wide, q_lower)
            scope = "company"
        else:
            rows = await _run(_search_direct_reports, uid, q_lower)
            scope = "direct"

        uids = [r["user_id"] for r in rows]
        enriched = await _run(_enrich_search_results, uids, rows)

    except Exception as exc:
        logger.warning("Search failed uid=%s q=%s: %s", uid, q, exc)
        return {"employees": [], "search_scope": "error"}

    return {
        "employees": enriched,
        "search_scope": scope,
    }
