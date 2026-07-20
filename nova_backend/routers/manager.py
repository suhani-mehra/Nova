"""
routers/manager.py
Manager endpoints:
  /api/manager/overview       — company-wide, exec managers only
  /api/manager/your-team      — direct reports, any manager
  /api/manager/people/search  — search (exec: company-wide/recursive; else direct)
"""

import asyncio
import logging
import zlib as _zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.auth import CurrentUser, get_current_user
from core.database import query as _query
from core.config import settings
from core.queries import get_direct_reports
from services.team_service import get_at_risk_employees

logger = logging.getLogger(__name__)
router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=8)

_trend_computing = False          # guard against concurrent AI trend recomputes
_team_snapshot_computing = False  # guard against concurrent team snapshot recomputes
_swr_inflight: set = set()        # cache keys with a background recompute in flight


def _swr(cache_key: str, compute_fn, fallback):
    """
    Stale-while-revalidate read for an expensive company-wide stat.

    Returns the fresh cached value if present. If the cache is expired (or
    missing), returns the stale value immediately and triggers a single
    background recompute — so the request never blocks on a full company scan.
    Falls back to `fallback` only when nothing has ever been cached.
    """
    from nova_db.gpt_cache import get_cache, get_cache_stale

    fresh = get_cache(cache_key)
    if fresh:
        return fresh["result"]

    if cache_key not in _swr_inflight:
        _swr_inflight.add(cache_key)

        def _recompute():
            try:
                compute_fn()  # recomputes and writes cache internally
            except Exception as exc:
                logger.warning("_swr recompute failed for %s: %s", cache_key, exc)
            finally:
                _swr_inflight.discard(cache_key)

        loop = asyncio.get_event_loop()
        loop.run_in_executor(_executor, _recompute)

    stale = get_cache_stale(cache_key)
    return stale["result"] if stale else fallback


# ── Exec access sets ──────────────────────────────────────────────────────────

# Exec profiles: get the company-wide Overview tab and company-wide people search.
# Every other manager searches only their own org subtree (direct + indirect
# reports) — see _search_recursive_org. Sourced from .env (EXEC_USER_IDS) so no
# privileged IDs are hardcoded in scanned source.
EXEC_USER_IDS: set[int] = set(settings.exec_user_ids)


def _safe_log(value) -> str:
    """Neutralize user-supplied text before it goes into a log line: strip
    CR/LF (prevents log forging / CWE-117) and cap length. Display/search
    behavior is unaffected — this is only for what gets logged."""
    s = str(value).replace("\r", " ").replace("\n", " ")
    return s[:100]


def _is_exec_manager(user: CurrentUser) -> bool:
    """
    True only for exec-level *managers* — the audience for the company-wide
    Overview tab. Effective exec status is an admin exec override if one exists
    for the user (see nova_db/admin_overrides), otherwise membership in
    EXEC_USER_IDS (.env). Either way the user must actually be a manager
    (user.role, itself override-aware). An explicit is_exec=False override can
    therefore revoke exec status even for someone in EXEC_USER_IDS.
    """
    from nova_db.admin_overrides import get_exec_overrides
    uid = user.classmate_user_id
    overrides = get_exec_overrides()
    exec_flag = overrides[uid] if uid in overrides else (uid in EXEC_USER_IDS)
    return exec_flag and user.role in ("manager", "both")


def _get_all_active_uids() -> list[int]:
    """Fetch all active user IDs from Fabric (the full 7225 population)."""
    from core.queries import _DEDUP_CTE
    try:
        rows = _query(
            _DEDUP_CTE + """
            SELECT user_id FROM latest_profiles WHERE rn = 1
            """
        )
        return [int(r["user_id"]) for r in rows if r["user_id"]]
    except Exception as exc:
        logger.warning("_get_all_active_uids failed, falling back to tier_scores table: %s", exc)
        from nova_db.tier_scores import get_all_tier_scores
        return list(get_all_tier_scores().keys())


def _prewarm_classify_cache(chunk_size: int = 200):
    """
    Background job: recompute classify_{uid} for every active user whose cache
    is currently missing. Covers the full population (~7225), not just those
    with credits. Runs in chunks to avoid oversized IN clauses.
    """
    from nova_db.gpt_cache import get_cache
    from services.skill_service import get_team_skill_scores

    all_uids = _get_all_active_uids()
    cold = [uid for uid in all_uids if not get_cache(f"classify_{uid}")]
    logger.info("classify pre-warm: %d cold out of %d total", len(cold), len(all_uids))
    for i in range(0, len(cold), chunk_size):
        chunk = cold[i : i + chunk_size]
        try:
            get_team_skill_scores(chunk)
            logger.info("classify pre-warm: chunk %d–%d done", i, i + len(chunk))
        except Exception as exc:
            logger.warning("classify pre-warm chunk %d failed: %s", i, exc)
    logger.info("classify pre-warm complete")


def _prewarm_streak_cache(chunk_size: int = 500):
    """
    Background job: batch-compute streak_{uid} for all active users using 2
    Fabric queries per chunk instead of 2 per user.
    """
    from nova_db.gpt_cache import get_cache, set_cache
    from datetime import date, timedelta

    all_uids = _get_all_active_uids()
    cold = [uid for uid in all_uids if not get_cache(f"streak_{uid}")]
    logger.info("streak pre-warm: %d cold out of %d total", len(cold), len(all_uids))

    today  = date.today()
    monday = today - timedelta(days=today.weekday())

    for i in range(0, len(cold), chunk_size):
        chunk = cold[i : i + chunk_size]
        # Placeholder count for IN(...), not a value — each id is still bound
        # through the parameterised '?' slots below, never concatenated.
        placeholders = ",".join("?" * len(chunk))
        try:
            activity_rows = _query(
                f"""
                SELECT DISTINCT user_id, date(activity_date) AS activity_date
                FROM (
                    SELECT user_id, credit_date AS activity_date
                    FROM fact_classmate_learning_credit
                    WHERE user_id IN ({placeholders}) AND is_deleted=0 AND duration>0
                    UNION
                    SELECT user_id, date(modified_on)
                    FROM fact_classmate_user_skill_status
                    WHERE user_id IN ({placeholders}) AND is_deleted=0 AND is_active=1
                    UNION
                    SELECT user_id, attended_date
                    FROM fact_classmate_self_study
                    WHERE user_id IN ({placeholders}) AND status=2 AND is_deleted=0
                ) src
                WHERE activity_date IS NOT NULL
                  AND activity_date >= date('now', '-365 days')
                """,
                tuple(chunk) * 3,
            )
            week_rows = _query(
                f"""
                SELECT user_id, SUM(duration) AS total_dur
                FROM fact_classmate_learning_credit
                WHERE user_id IN ({placeholders}) AND is_deleted=0 AND duration>0
                  AND credit_date >= ? AND credit_date <= ?
                GROUP BY user_id
                """,
                tuple(chunk) + (monday, monday + timedelta(days=6)),
            )
        except Exception as exc:
            logger.warning("streak pre-warm chunk %d failed: %s", i, exc)
            continue

        uid_active: dict[int, set] = {uid: set() for uid in chunk}
        for r in activity_rows:
            uid = r["user_id"]
            d   = r["activity_date"]
            if d and uid in uid_active:
                uid_active[uid].add(d.date() if hasattr(d, "date") else d)

        uid_week_secs = {r["user_id"]: int(r["total_dur"] or 0) for r in week_rows}

        for uid in chunk:
            active_days = uid_active[uid]

            streak = 0
            check  = today if today in active_days else today - timedelta(days=1)
            while check in active_days:
                streak += 1
                check  -= timedelta(days=1)

            week_map = [(monday + timedelta(days=j)) in active_days for j in range(7)]

            secs  = uid_week_secs.get(uid, 0)
            h, r  = divmod(secs, 3600)
            learning_time = f"{h}h {r // 60}m"

            active_30 = sum(1 for j in range(30) if (today - timedelta(days=j)) in active_days)
            active_90 = sum(1 for j in range(90) if (today - timedelta(days=j)) in active_days)

            set_cache(f"streak_{uid}", {
                "current_streak":      streak,
                "week_map":            week_map,
                "learning_time":       learning_time,
                "active_days_last_30": active_30,
                "active_days_last_90": active_90,
            }, "computed", ttl_hours=25)

        logger.info("streak pre-warm: chunk %d–%d done", i, i + len(chunk))

    logger.info("streak pre-warm complete")


def _prewarm_manager_people_cache(chunk_size: int = 20):
    """
    Pre-warm people_list_{manager_id}_all for every active manager.
    Called at 3 AM after classify/streak/tier caches are warm,
    so _build_people_list() runs cheaply (no per-user Fabric queries).
    Also populates direct_reports_{manager_id} as a side effect.
    """
    mgr_rows = _query("""
        SELECT DISTINCT manager AS mgr_id
        FROM dim_classmate_employee_profile
        WHERE etl_isactive = 1 AND is_active = 1 AND is_deleted = 0
          AND manager IS NOT NULL
          AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
    """)
    if not mgr_rows:
        logger.info("manager people pre-warm: no managers found")
        return

    mgr_ids = [r["mgr_id"] for r in mgr_rows if r["mgr_id"]]
    logger.info("manager people pre-warm: %d managers", len(mgr_ids))

    for i in range(0, len(mgr_ids), chunk_size):
        chunk = mgr_ids[i: i + chunk_size]
        for manager_id in chunk:
            try:
                _build_people_list(manager_id, "all")
            except Exception as exc:
                logger.warning("manager people pre-warm failed for mgr=%s: %s", manager_id, exc)
        logger.info(
            "manager people pre-warm: %d/%d done",
            min(i + chunk_size, len(mgr_ids)),
            len(mgr_ids),
        )

    logger.info("manager people pre-warm complete")


# ── Core helpers ──────────────────────────────────────────────────────────────

async def _run(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


def _require_manager(user: CurrentUser):
    if user.classmate_user_id is not None and user.role not in ("manager", "both"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager role required",
        )


# ── Company-wide stat helpers ─────────────────────────────────────────────────

def _get_company_headcount() -> int:
    rows = _query("""
        SELECT COUNT(DISTINCT user_id) AS n
        FROM dim_classmate_employee_profile
        WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
          AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
    """)
    return int(rows[0]["n"] or 0) if rows else 0


def _get_company_active_this_week() -> int:
    """
    Active learners this week = active employees with at least one active day in
    the current week, using the same 3-source activity union as the per-user
    streak (learning credit w/ duration, skill-status update, attended
    self-study). Counted directly from Fabric in one query so it never depends on
    the per-user streak_{uid} caches being warm (which previously made this read
    0 at cold start and stay cached at 0).
    """
    monday = date.today() - timedelta(days=date.today().weekday())
    sunday = monday + timedelta(days=6)
    rows = _query(
        """
        SELECT COUNT(DISTINCT src.user_id) AS n
        FROM (
            SELECT user_id
            FROM fact_classmate_learning_credit
            WHERE is_deleted = 0 AND duration > 0
              AND credit_date >= ? AND credit_date <= ?
            UNION
            SELECT user_id
            FROM fact_classmate_user_skill_status
            WHERE is_deleted = 0 AND is_active = 1
              AND date(modified_on) >= ? AND date(modified_on) <= ?
            UNION
            SELECT user_id
            FROM fact_classmate_self_study
            WHERE status = 2 AND is_deleted = 0
              AND attended_date >= ? AND attended_date <= ?
        ) src
        WHERE src.user_id IN (
            SELECT DISTINCT user_id
            FROM dim_classmate_employee_profile
            WHERE etl_isactive = 1 AND is_active = 1 AND is_deleted = 0
              AND user_id IS NOT NULL
              AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
              AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
        )
        """,
        (monday, sunday, monday, sunday, monday, sunday),
    )
    return int(rows[0]["n"] or 0) if rows else 0


def _get_team_active_this_week(uids: list) -> int:
    """
    Active learners this week within a specific set of users (a manager's direct
    reports). Same 3-source activity union as `_get_company_active_this_week()`
    but scoped to `uids`. Returns 0 for an empty list (avoids `IN ()`).
    """
    if not uids:
        return 0
    monday = date.today() - timedelta(days=date.today().weekday())
    sunday = monday + timedelta(days=6)
    # Placeholder count for IN(...), not a value — each id is still bound
    # through the parameterised '?' slots below, never concatenated.
    placeholders = ",".join("?" * len(uids))
    rows = _query(
        f"""
        SELECT COUNT(DISTINCT src.user_id) AS n
        FROM (
            SELECT user_id
            FROM fact_classmate_learning_credit
            WHERE is_deleted = 0 AND duration > 0
              AND credit_date >= ? AND credit_date <= ?
            UNION
            SELECT user_id
            FROM fact_classmate_user_skill_status
            WHERE is_deleted = 0 AND is_active = 1
              AND date(modified_on) >= ? AND date(modified_on) <= ?
            UNION
            SELECT user_id
            FROM fact_classmate_self_study
            WHERE status = 2 AND is_deleted = 0
              AND attended_date >= ? AND attended_date <= ?
        ) src
        WHERE src.user_id IN ({placeholders})
        """,
        (monday, sunday, monday, sunday, monday, sunday, *uids),
    )
    return int(rows[0]["n"] or 0) if rows else 0


def _get_team_courses_completed_this_week(uids: list) -> int:
    """
    Count of course completions this week among a set of users (a manager's
    direct reports). Returns 0 for an empty list.
    """
    if not uids:
        return 0
    monday = date.today() - timedelta(days=date.today().weekday())
    sunday = monday + timedelta(days=6)
    # Placeholder count for IN(...), not a value — each id is still bound
    # through the parameterised '?' slots below, never concatenated.
    placeholders = ",".join("?" * len(uids))
    rows = _query(
        f"""
        SELECT COUNT(*) AS n
        FROM vw_classmate_trainings
        WHERE status = 4052
          AND completed_on >= ? AND completed_on <= ?
          AND user_id IN ({placeholders})
        """,
        (monday, sunday, *uids),
    )
    return int(rows[0]["n"] or 0) if rows else 0


def _get_company_avg_credits_this_quarter() -> float:
    rows = _query("""
        SELECT AVG(s.credits) AS avg_c FROM (
            SELECT user_id, SUM(learning_credits) AS credits
            FROM vw_classmate_trainings
            WHERE status=4052 AND completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-90 days')
              AND user_id IN (
                  SELECT DISTINCT user_id
                  FROM dim_classmate_employee_profile
                  WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
                    AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
                    AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
              )
            GROUP BY user_id
        ) s
    """)
    return round(float(rows[0]["avg_c"] or 0), 1) if rows else 0.0


def _compute_company_overview_stats() -> dict:
    """
    Bundles the company-wide overview metrics into one cached call.
    """
    from nova_db.gpt_cache import get_cache, set_cache
    CACHE_KEY = "company_overview_stats"
    cached = get_cache(CACHE_KEY)
    if cached:
        return cached["result"]
    try:
        headcount   = _get_company_headcount()
        active_week = _get_company_active_this_week()
        avg_credits = _get_company_avg_credits_this_quarter()

        result = {
            "headcount":                headcount,
            "active_this_week":         active_week,
            "avg_credits_this_quarter": avg_credits,
        }
        set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
        return result
    except Exception as exc:
        logger.warning("_compute_company_overview_stats failed: %s", exc)
        return {
            "headcount": 0, "active_this_week": 0,
            "avg_credits_this_quarter": 0.0,
        }


def _compute_company_retention() -> dict:
    """
    % active learners = share of all employees with any learning activity in the
    last 30 days. Trend = this 30-day window vs the prior 30-day window
    (this month vs last month), in percentage points.
    Cache key kept as "retention_snapshot" / shape {rate, trend_pct, trend_dir}.
    """
    from nova_db.gpt_cache import get_cache, set_cache
    CACHE_KEY = "retention_snapshot"
    cached = get_cache(CACHE_KEY)
    if cached:
        return cached["result"]

    try:
        emp_rows = _query("""
            SELECT DISTINCT user_id
            FROM dim_classmate_employee_profile
            WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
              AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
              AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
        """)
        all_uids = {r["user_id"] for r in emp_rows if r["user_id"]}
        headcount = len(all_uids)

        rows = _query("""
            SELECT DISTINCT user_id,
                CASE
                    WHEN credit_date >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
                    THEN 'cur'
                    WHEN credit_date >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-60 days')
                         AND credit_date < strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
                    THEN 'prev'
                END AS window
            FROM fact_classmate_learning_credit
            WHERE is_deleted=0
              AND credit_date >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-60 days')
              AND user_id IS NOT NULL
        """)
        cur: set = set()
        prev: set = set()
        for r in rows:
            uid = r["user_id"]
            if uid not in all_uids:
                continue
            if r["window"] == "cur":    cur.add(uid)
            elif r["window"] == "prev": prev.add(uid)

        rate      = round(len(cur) / headcount * 100, 1) if headcount else 0.0
        prev_rate = round(len(prev) / headcount * 100, 1) if headcount else 0.0
        trend_pct = round(rate - prev_rate, 1)
        trend_dir = "up" if trend_pct > 0 else "down" if trend_pct < 0 else "flat"

        result = {"rate": rate, "trend_pct": trend_pct, "trend_dir": trend_dir}
        set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
        return result
    except Exception as exc:
        logger.warning("_compute_company_retention failed: %s", exc)
        return {"rate": 0.0, "trend_pct": 0.0, "trend_dir": "flat"}


def _compute_company_at_risk_count() -> dict:
    """
    At risk = weighted health score below 0.20, where
        health = 0.7 * (AI proficiency / 100) + 0.3 * (active this week ? 1 : 0)
    AI proficiency comes from the (cache-backed) team skill scores; active-this-week
    comes from the streak cache (week_map).
    Returns {"count": int, "trend_pct": float, "trend_dir": str}.
    Trend compares against a weekly baseline stored in gpt_cache (7-day TTL).
    The baseline is only written when it doesn't already exist, so after one week
    it naturally resets and the next run becomes the new baseline.
    """
    from nova_db.gpt_cache import get_cache, set_cache
    from services.skill_service import get_team_skill_scores
    CACHE_KEY = "company_at_risk_count"
    cached = get_cache(CACHE_KEY)
    if cached:
        result = cached["result"]
        if isinstance(result, dict):
            return result

    try:
        all_uids = _get_all_active_uids()
        if not all_uids:
            return {"count": 0, "trend_pct": 0.0, "trend_dir": "flat"}

        scores = get_team_skill_scores(all_uids)  # {uid: {"AI": 0..100, ...}}

        count = 0
        for uid in all_uids:
            ai = scores.get(uid, {}).get("AI", 0.0)
            sc = get_cache(f"streak_{uid}")
            active = bool(sc and any(sc["result"].get("week_map") or []))
            health = 0.7 * (ai / 100.0) + 0.3 * (1.0 if active else 0.0)
            if health < 0.20:
                count += 1

        # Trend vs weekly baseline (only set baseline when it has expired)
        prev_snap = get_cache("company_at_risk_prev")
        if prev_snap is not None:
            prev_count = int(prev_snap["result"])
            trend_pct  = round((count - prev_count) / max(prev_count, 1) * 100, 1)
            trend_dir  = "up" if trend_pct > 0 else "down" if trend_pct < 0 else "flat"
        else:
            trend_pct = 0.0
            trend_dir = "flat"
            # Write baseline — only once; it will expire in 7 days and naturally reset
            set_cache("company_at_risk_prev", count, "computed", ttl_hours=24 * 7)

        result = {"count": count, "trend_pct": trend_pct, "trend_dir": trend_dir}
        set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
        return result
    except Exception as exc:
        logger.warning("_compute_company_at_risk_count failed: %s", exc)
        return {"count": 0, "trend_pct": 0.0, "trend_dir": "flat"}


# ── Background jobs ───────────────────────────────────────────────────────────

def _quarter_start(q_end: date) -> date:
    """Return the first day of the quarter that ends on q_end."""
    start_month = ((q_end.month - 1) // 3) * 3 + 1
    return date(q_end.year, start_month, 1)


def _compute_quarterly_ai_proficiency() -> list:
    """
    Computes 6 quarters of AI proficiency % AND % active learners across all active employees.
    Returns [{"month": "Q3 '24", "credits": 12.5, "active_pct": 67.3}, ...] — cached 25 h.
    """
    global _trend_computing
    from nova_db.gpt_cache import get_cache, set_cache
    from nova_db.course_scores import get_scores_for_items
    from services.skill_service import MASTERY_THRESHOLD, MASTERY_POWER

    CACHE_KEY = "ai_proficiency_trend"
    cached = get_cache(CACHE_KEY)
    if cached:
        _trend_computing = False
        return cached["result"]

    _trend_computing = True
    logger.info("ai_proficiency_trend: computing quarterly trend (company-wide)")

    emp_rows = _query("""
        SELECT DISTINCT user_id
        FROM dim_classmate_employee_profile
        WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
          AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
    """)
    all_uids = {r["user_id"] for r in emp_rows if r["user_id"]}
    total = len(all_uids)
    if not total:
        _trend_computing = False
        return []

    try:
        training_rows = _query("""
            SELECT user_id, second_level_category_id AS item_id,
                   completed_on
            FROM vw_classmate_trainings
            WHERE status=4052 AND user_id IS NOT NULL
              AND second_level_category_id IS NOT NULL AND completed_on IS NOT NULL
        """)
    except Exception as exc:
        logger.warning("ai_proficiency_trend: trainings query failed: %s", exc)
        training_rows = []

    try:
        cert_rows = _query("""
            SELECT fc.user_id, fc.certificate_id AS item_id,
                   fc.completion_date AS completed_on
            FROM fact_classmate_certification fc
            WHERE fc.status=2 AND fc.is_active=1 AND fc.is_deleted=0
              AND fc.user_id IS NOT NULL AND fc.certificate_id IS NOT NULL
              AND fc.completion_date IS NOT NULL
        """)
    except Exception as exc:
        logger.warning("ai_proficiency_trend: certs query failed: %s", exc)
        cert_rows = []

    try:
        learning_credit_rows = _query("""
            SELECT user_id, topic AS name, MIN(credit_date) AS completed_on
            FROM fact_classmate_learning_credit
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
        learning_credit_rows = []

    def _tid(topic: str) -> int:
        return _zlib.crc32(topic.encode("utf-8", errors="replace")) & 0x7FFFFFFF

    all_events: list = []
    lookup_pairs: set = set()

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

    for r in learning_credit_rows:
        uid  = r["user_id"]
        name = r.get("name") or ""
        co   = r["completed_on"]
        if uid not in all_uids or not name:
            continue
        co_date = co.date() if hasattr(co, "date") else co
        pair = ("lc", _tid(name))
        all_events.append((uid, pair, co_date))
        lookup_pairs.add(pair)

    score_map = get_scores_for_items(list(lookup_pairs))

    # Per-user AI contributions over time
    user_ai: dict = {uid: [] for uid in all_uids}
    seen_lc_per_user: dict = {uid: set() for uid in all_uids}
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

    # Build 7 quarters (oldest = warm-up for retention context, not displayed).
    # The warm-up quarter gives Q1 of the displayed range a meaningful retention
    # rate instead of a forced 0, which would create a misleading spike at Q2.
    today = date.today()
    q_end_dates = [(1, 3, 31), (2, 6, 30), (3, 9, 30), (4, 12, 31)]
    q_idx = (today.month - 1) // 3
    yr = today.year
    # The chart is "measured at each quarter end", so exclude the current
    # in-progress quarter (it has partial data and would dip sharply). Shift the
    # window back to end at the last COMPLETED quarter.
    _, _cm, _cd = q_end_dates[q_idx]
    if date(yr, _cm, _cd) > today:
        q_idx -= 1
        if q_idx < 0:
            q_idx = 3
            yr -= 1
    quarters: list = []
    for _ in range(7):  # 7 = 6 displayed + 1 warm-up
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
    # quarters[0] is the warm-up (not in result); quarters[1:] are the 6 displayed

    # Track which users were active (any learning) in each of the 7 quarters
    q_starts = [_quarter_start(q_end) for _, q_end in quarters]
    user_active_in_quarter: dict = {label: set() for label, _ in quarters}
    for uid, pair, co_date in all_events:
        for i, (label, q_end) in enumerate(quarters):
            if q_starts[i] <= co_date <= q_end:
                user_active_in_quarter[label].add(uid)
                break

    # Build result — skip the warm-up quarter (index 0), display the last 6.
    # Second line is % active learners = share of employees with any learning
    # activity in that quarter.
    result = []
    for label, cutoff in quarters[1:]:
        proficient = sum(
            1 for uid in all_uids
            if min(100.0, (
                sum(v for d, v in user_ai[uid] if d <= cutoff) / MASTERY_THRESHOLD
            ) ** MASTERY_POWER * 100) >= settings.ai_proficiency_min_score
        )
        pct = round(proficient / total * 100, 1)
        active_pct = round(len(user_active_in_quarter[label]) / total * 100, 1)
        result.append({
            "month":      label,
            "credits":    pct,
            "active_pct": active_pct,
        })

    logger.info(
        "ai_proficiency_trend: complete — %d employees, latest: %.1f%% AI-proficient, %.1f%% active",
        total,
        result[-1]["credits"] if result else 0.0,
        result[-1]["active_pct"] if result else 0.0,
    )
    set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
    _trend_computing = False
    return result


def _default_quarterly_trend() -> list:
    """Placeholder quarters (all zeros) shown while background job computes."""
    today = date.today()
    q_idx = (today.month - 1) // 3
    yr = today.year
    quarters = []
    for _ in range(6):
        label = f"Q{q_idx + 1} '{str(yr)[2:]}"
        quarters.append({"month": label, "credits": 0.0, "active_pct": 0.0})
        q_idx -= 1
        if q_idx < 0:
            q_idx = 3
            yr -= 1
    quarters.reverse()
    return quarters


def _compute_manager_team_snapshot() -> list:
    """
    Computes each manager's team average skill score (mean of all 5 skill
    verticals, averaged across the manager's direct reports) for the Team
    Leaderboard. A "team" = the direct reports of one manager. Cached 25 h under
    key "team_leaderboard_by_manager". Runs at startup.
    """
    global _team_snapshot_computing
    from nova_db.gpt_cache import get_cache, set_cache
    from core.queries import _DEDUP_CTE, get_employee_profile

    CACHE_KEY = "team_leaderboard_by_manager"
    cached = get_cache(CACHE_KEY)
    if cached:
        _team_snapshot_computing = False
        return cached["result"]

    _team_snapshot_computing = True
    logger.info("team_leaderboard: computing per-manager team skill averages")

    try:
        emp_rows = _query(
            _DEDUP_CTE + """
            SELECT user_id, manager
            FROM latest_profiles
            WHERE rn=1 AND user_id IS NOT NULL AND manager IS NOT NULL
            """
        )
    except Exception as exc:
        logger.warning("team_leaderboard: employee query failed: %s", exc)
        _team_snapshot_computing = False
        return []

    if not emp_rows:
        _team_snapshot_computing = False
        return []

    # Group direct reports under each manager.
    mgr_uids: dict = defaultdict(list)
    all_uid_list: list = []
    for r in emp_rows:
        mgr_uids[r["manager"]].append(r["user_id"])
        all_uid_list.append(r["user_id"])

    # Per-employee skill score = mean of the 5 normalized axis scores (the same
    # building block used as tier_score's skill component). Read from the warm
    # classify_{uid} cache first, then batch-compute any misses.
    uid_skill: dict = {}
    uncached_uids: list = []
    for uid in all_uid_list:
        c = get_cache(f"classify_{uid}")
        if c:
            res = c.get("result", {})
            this_month = res.get("this_month", [])
            uid_skill[uid] = round(sum(this_month) / len(this_month), 1) if this_month else 0.0
        else:
            uncached_uids.append(uid)

    if uncached_uids:
        try:
            from services.skill_service import get_team_skill_scores, AXES
            BATCH = 500
            for i in range(0, len(uncached_uids), BATCH):
                batch = uncached_uids[i: i + BATCH]
                team_norm = get_team_skill_scores(batch)
                for uid in batch:
                    axis_scores = team_norm.get(uid, {})
                    vals = [float(axis_scores.get(ax, 0.0)) for ax in AXES]
                    uid_skill[uid] = round(sum(vals) / len(vals), 1) if vals else 0.0
        except Exception as exc:
            logger.warning("team_leaderboard: skill scores failed for uncached batch: %s", exc)
            for uid in uncached_uids:
                uid_skill.setdefault(uid, 0.0)

    # Bayesian shrinkage toward the company-wide average, weighted by team size.
    # A raw team average lets tiny teams (1–3 reports) top the board on a lucky
    # couple of high performers; blending each team's average with the company
    # average — weighted by headcount — pulls small, low-evidence teams toward the
    # center while leaving large teams (n >> K) essentially unchanged. K ≈ the mean
    # team size in this data, so teams above average size keep most of their own
    # signal. Retune K to shift how hard small teams get pulled down.
    company_avg = sum(uid_skill.values()) / len(uid_skill) if uid_skill else 0.0
    K = 8

    result = []
    for manager_id, uids_in_team in mgr_uids.items():
        if not uids_in_team:
            continue
        n = len(uids_in_team)
        avg_skill = round(sum(uid_skill.get(uid, 0.0) for uid in uids_in_team) / n, 1)
        shrunk_skill = round((n * avg_skill + K * company_avg) / (n + K), 1)
        office = ""
        try:
            prof = get_employee_profile(None, manager_id)
            name = prof[0]["name"] if prof and prof[0].get("name") else f"Manager {manager_id}"
            raw_office = prof[0].get("office_name") if prof else None
            office = str(raw_office).strip().title() if raw_office else ""
        except Exception as exc:
            logger.warning("top_teams_radar: profile lookup failed for manager_id=%s: %s", manager_id, exc)
            name = f"Manager {manager_id}"
        result.append({
            "manager_id":        manager_id,        # kept so the radar-overlay helper can re-fetch members
            "name":              name,
            "office":            office,          # manager's office location (title-cased, "" if unknown)
            "headcount":         n,
            "avg_skill_pct":     shrunk_skill,   # shrunk score — what the leaderboard ranks/shows
            "raw_avg_skill_pct": avg_skill,      # unshrunk team average, kept for reference
        })

    result.sort(key=lambda x: x["avg_skill_pct"], reverse=True)

    set_cache(CACHE_KEY, result, "computed", ttl_hours=25)

    logger.info("team_leaderboard: complete — %d manager teams", len(result))
    _team_snapshot_computing = False
    return result


def _get_top_teams_with_radar() -> list:
    """Full 5-axis radar for the Top 5 leaderboard teams, for the "compare with
    top team" overlay on the Your Team page. Self-caching under "top_teams_radar"
    (25h) — identical for every manager (it's the company top 5), so it's computed
    once and read thereafter. Cheap: only 5 teams' worth of already-warm
    classify_{uid} data via get_team_skill_radar."""
    from nova_db.gpt_cache import get_cache, set_cache
    from services.skill_service import get_team_skill_radar

    cached = get_cache("top_teams_radar")
    if cached:
        return cached["result"]

    snapshot = _compute_manager_team_snapshot()  # returns cached list if warm
    out = []
    for team in snapshot[:5]:
        manager_id = team.get("manager_id")
        if manager_id is None:
            continue
        try:
            reports = get_direct_reports(None, manager_id)
            uids = [r["user_id"] for r in reports]
            radar = get_team_skill_radar(uids)
            out.append({
                "manager_id":    manager_id,
                "name":          team.get("name"),
                "avg_skill_pct": team.get("avg_skill_pct"),
                "axes":          radar.get("axes"),
                "this_month":    radar.get("this_month"),
            })
        except Exception as exc:
            logger.warning("top_teams_radar: failed for mgr %s: %s", manager_id, exc)

    set_cache("top_teams_radar", out, "computed", ttl_hours=25)
    return out


def _batch_ai_scores(uid_list: list, caller_label: str) -> dict:
    """AI score per uid — warm classify_{uid} cache first, batch-compute the
    rest via get_team_skill_scores. Shared by _compute_ai_proficiency_by_region,
    _compute_proficiency_by_vertical, _compute_specialization_landscape, and
    _compute_team_quadrant (previously duplicated identically in all four).
    `caller_label` tags the warning log so failures are traceable to the
    calling function."""
    from nova_db.gpt_cache import get_cache
    uid_ai: dict = {}
    uncached_uids: list = []
    for uid in uid_list:
        c = get_cache(f"classify_{uid}")
        if c:
            res = c.get("result", {})
            axes = res.get("axes", ["AI", "Cloud", "Frontend", "Backend", "Data"])
            this_month = res.get("this_month", [])
            try:
                ai_idx = axes.index("AI")
                uid_ai[uid] = float(this_month[ai_idx]) if ai_idx < len(this_month) else 0.0
            except (ValueError, IndexError) as exc:
                logger.warning("classify cache shape mismatch for uid=%s: %s", uid, exc)
                uid_ai[uid] = 0.0
        else:
            uncached_uids.append(uid)

    if uncached_uids:
        try:
            from services.skill_service import get_team_skill_scores
            BATCH = 500
            for i in range(0, len(uncached_uids), BATCH):
                batch = uncached_uids[i: i + BATCH]
                team_norm = get_team_skill_scores(batch)
                for uid in batch:
                    uid_ai[uid] = round(team_norm.get(uid, {}).get("AI", 0.0), 1)
        except Exception as exc:
            logger.warning("%s: skill scores failed for uncached batch: %s", caller_label, exc)
            for uid in uncached_uids:
                uid_ai.setdefault(uid, 0.0)

    return uid_ai


_region_snapshot_computing = False  # guard against concurrent region snapshot recomputes


def _compute_ai_proficiency_by_region() -> dict:
    """
    Computes, for each AI proficiency level (Professional/Specialist/Expert/
    Champion), what % of the whole company has reached that level — broken down
    by region (Asia / North America / Europe / Other).

    Levels are CUMULATIVE ("at least"): a Champion (score >= 65) is also counted
    at Expert, Specialist and Professional. "Professional" == the standard
    company-wide AI-proficient threshold (ai_proficiency_min_score).

    Returns:
        {
          "total": <company headcount>,
          "region_totals": {"asia": n, "na": n, "eu": n},
          "levels": [
            {"key","name","threshold","goal_pct","total_count","total_pct",
             "regions": {"asia":{"count","pct_of_company","pct_of_region"}, ...}},
            ...
          ],
        }
    Cached 25 h under key "ai_proficiency_by_region". Runs at startup/nightly.
    """
    global _region_snapshot_computing
    from nova_db.gpt_cache import get_cache, set_cache
    from core.queries import get_all_active_employee_regions
    from core.geo import continent_for, REGION_ORDER, REGION_LABELS

    CACHE_KEY = "ai_proficiency_by_region"
    cached = get_cache(CACHE_KEY)
    if cached:
        _region_snapshot_computing = False
        return cached["result"]

    _region_snapshot_computing = True
    logger.info("ai_proficiency_by_region: computing company-wide region split")

    try:
        emp_rows = get_all_active_employee_regions(None)
    except Exception as exc:
        logger.warning("ai_proficiency_by_region: employee query failed: %s", exc)
        _region_snapshot_computing = False
        return _default_region_snapshot()

    if not emp_rows:
        _region_snapshot_computing = False
        return _default_region_snapshot()

    uid_region: dict = {}
    all_uid_list: list = []
    for r in emp_rows:
        uid = r["user_id"]
        reg = continent_for(r.get("country_code"))
        # None (NULL / unmapped country_code — placeholder rows) is not a
        # rendered region; drop those employees from the chart entirely.
        if reg not in REGION_ORDER:
            continue
        uid_region[uid] = reg
        all_uid_list.append(uid)

    # Read AI scores from gpt_cache first, then batch-compute the uncached —
    # same warm-cache path used elsewhere so scoring logic is never duplicated.
    uid_ai = _batch_ai_scores(all_uid_list, "ai_proficiency_by_region")

    total = len(all_uid_list)
    region_totals = {reg: 0 for reg in REGION_ORDER}
    for uid in all_uid_list:
        region_totals[uid_region[uid]] += 1

    levels_cfg = settings.ai_proficiency_levels
    goals_cfg = settings.ai_proficiency_level_goals

    levels = []
    for key in ("professional", "specialist", "expert", "champion"):
        threshold = levels_cfg[key]
        per_region = {reg: 0 for reg in REGION_ORDER}
        for uid in all_uid_list:
            if uid_ai.get(uid, 0.0) >= threshold:
                per_region[uid_region[uid]] += 1
        total_count = sum(per_region.values())
        regions_out = {}
        for reg in REGION_ORDER:
            cnt = per_region[reg]
            reg_total = region_totals[reg]
            regions_out[reg] = {
                "label":          REGION_LABELS[reg],
                "count":          cnt,
                "pct_of_company": round(cnt / total * 100, 1) if total else 0.0,
                "pct_of_region":  round(cnt / reg_total * 100, 1) if reg_total else 0.0,
            }
        levels.append({
            "key":         key,
            "name":        key.capitalize(),
            "threshold":   threshold,
            "goal_pct":    goals_cfg[key],
            "total_count": total_count,
            "total_pct":   round(total_count / total * 100, 1) if total else 0.0,
            "regions":     regions_out,
        })

    result = {
        "total":         total,
        "region_totals": {reg: region_totals[reg] for reg in REGION_ORDER},
        "region_labels": {reg: REGION_LABELS[reg] for reg in REGION_ORDER},
        "levels":        levels,
    }

    set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
    logger.info(
        "ai_proficiency_by_region: complete — %d employees, professional %.1f%%",
        total, levels[0]["total_pct"] if levels else 0.0,
    )
    _region_snapshot_computing = False
    return result


def _default_region_snapshot() -> dict:
    """Empty placeholder shown while the background job computes."""
    from core.geo import REGION_ORDER, REGION_LABELS
    from core.config import settings as _s
    return {
        "total": 0,
        "region_totals": {reg: 0 for reg in REGION_ORDER},
        "region_labels": {reg: REGION_LABELS[reg] for reg in REGION_ORDER},
        "levels": [
            {
                "key": key, "name": key.capitalize(),
                "threshold": _s.ai_proficiency_levels[key],
                "goal_pct": _s.ai_proficiency_level_goals[key],
                "total_count": 0, "total_pct": 0.0,
                "regions": {reg: {"label": REGION_LABELS[reg], "count": 0,
                                   "pct_of_company": 0.0, "pct_of_region": 0.0}
                            for reg in REGION_ORDER},
            }
            for key in ("professional", "specialist", "expert", "champion")
        ],
    }


# ── Proficiency by vertical (exec Overview) ───────────────────────────────────

_vertical_snapshot_computing = False  # guard against concurrent recomputes


def _compute_proficiency_by_vertical() -> dict:
    """
    For each AI proficiency level (Professional/Specialist/Expert/Champion),
    the % of each business-unit industry group's employees who have reached
    that level. Levels are CUMULATIVE ("at least"), mirroring
    _compute_ai_proficiency_by_region.

    Joins the employee_role table (employee_id → vertical_name) to the latest
    active profile (employee_id → user_id), maps vertical_name → group via
    core.verticals.group_for, and reuses the same warm classify_{uid} AI-score
    path as _compute_ai_proficiency_by_region (no rescoring).

    Returns:
        {
          "groups": [<group name>, ...],           # all groups, stable order
          "group_totals": {group: n},
          "levels": [
            {"key","name","threshold","goal_pct","total_count","total_pct",
             "verticals": {group: {"count","total","pct_of_company","pct_of_group"}}},
            ...
          ],
        }
    Cached 25 h under "proficiency_by_vertical". Runs at startup/nightly.
    """
    global _vertical_snapshot_computing
    from nova_db.gpt_cache import get_cache, set_cache
    from core.queries import _DEDUP_CTE
    from core.verticals import group_for

    CACHE_KEY = "proficiency_by_vertical"
    cached = get_cache(CACHE_KEY)
    if cached:
        _vertical_snapshot_computing = False
        return cached["result"]

    _vertical_snapshot_computing = True
    logger.info("proficiency_by_vertical: computing company-wide vertical split")

    try:
        rows = _query(
            _DEDUP_CTE + """
            SELECT lp.user_id AS user_id, er.vertical_name AS vertical_name
            FROM employee_role er
            JOIN latest_profiles lp
              ON UPPER(TRIM(lp.employee_id)) = UPPER(TRIM(er.employee_id))
            WHERE lp.rn = 1
            """
        )
    except Exception as exc:
        logger.warning("proficiency_by_vertical: employee_role join failed: %s", exc)
        _vertical_snapshot_computing = False
        return _default_proficiency_by_vertical()

    if not rows:
        _vertical_snapshot_computing = False
        return _default_proficiency_by_vertical()

    uid_group: dict = {}
    all_uid_list: list = []
    for r in rows:
        uid = r["user_id"]
        if uid is None or uid in uid_group:
            continue
        uid_group[uid] = group_for(r.get("vertical_name"))
        all_uid_list.append(uid)

    # AI scores: warm classify_{uid} cache first, batch-compute the uncached —
    # identical path to _compute_ai_proficiency_by_region.
    uid_ai = _batch_ai_scores(all_uid_list, "proficiency_by_vertical")

    total = len(all_uid_list)
    group_totals: dict = {}
    for uid in all_uid_list:
        g = uid_group[uid]
        group_totals[g] = group_totals.get(g, 0) + 1
    groups = sorted(group_totals)

    levels_cfg = settings.ai_proficiency_levels
    goals_cfg = settings.ai_proficiency_level_goals

    levels = []
    for key in ("professional", "specialist", "expert", "champion"):
        threshold = levels_cfg[key]
        per_group = {g: 0 for g in groups}
        for uid in all_uid_list:
            if uid_ai.get(uid, 0.0) >= threshold:
                per_group[uid_group[uid]] += 1
        total_count = sum(per_group.values())
        verticals_out = {}
        for g in groups:
            cnt = per_group[g]
            g_total = group_totals[g]
            verticals_out[g] = {
                "count":          cnt,
                "total":          g_total,
                "pct_of_company": round(cnt / total * 100, 1) if total else 0.0,
                "pct_of_group":   round(cnt / g_total * 100, 1) if g_total else 0.0,
            }
        levels.append({
            "key":         key,
            "name":        key.capitalize(),
            "threshold":   threshold,
            "goal_pct":    goals_cfg[key],
            "total_count": total_count,
            "total_pct":   round(total_count / total * 100, 1) if total else 0.0,
            "verticals":   verticals_out,
        })

    result = {
        "groups":       groups,
        "group_totals": group_totals,
        "levels":       levels,
    }
    set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
    logger.info(
        "proficiency_by_vertical: complete — %d groups, professional %.1f%%",
        len(groups), levels[0]["total_pct"] if levels else 0.0,
    )
    _vertical_snapshot_computing = False
    return result


def _default_proficiency_by_vertical() -> dict:
    """Empty placeholder shown while the background job computes."""
    from core.config import settings as _s
    return {
        "groups": [],
        "group_totals": {},
        "levels": [
            {
                "key": key, "name": key.capitalize(),
                "threshold": _s.ai_proficiency_levels[key],
                "goal_pct": _s.ai_proficiency_level_goals[key],
                "total_count": 0, "total_pct": 0.0,
                "verticals": {},
            }
            for key in ("professional", "specialist", "expert", "champion")
        ],
    }


_specialization_computing = False  # guard against concurrent recomputes


def _compute_specialization_landscape() -> dict:
    """
    Distribution of the AI-proficient population across six role groups (A–F).

    Classifies each employee (via core.roles.classify_role, keyed on
    department_name + designation overrides + keyword fallback) and, among those
    who are AI-proficient (AI axis >= ai_proficiency_min_score), computes each
    group's SHARE of the total AI-proficient headcount (segments sum to 100%).

    Returns {"tracks": [{track, pct, earners, col}]} sorted by pct desc
    (earners = AI-proficient count in that group). Cached 25 h under
    "specialization_landscape".
    """
    global _specialization_computing
    from nova_db.gpt_cache import get_cache, set_cache
    from core.queries import _DEDUP_CTE
    from core.roles import classify_role, GROUP_ORDER, GROUP_LABELS, GROUP_COLORS

    CACHE_KEY = "specialization_landscape"
    cached = get_cache(CACHE_KEY)
    if cached:
        _specialization_computing = False
        return cached["result"]

    _specialization_computing = True
    logger.info("specialization_landscape: computing company-wide role-group split")

    try:
        rows = _query(
            _DEDUP_CTE + """
            SELECT lp.user_id       AS user_id,
                   er.department_name  AS department_name,
                   er.designation_title AS designation_title,
                   er.known_as_name   AS known_as_name,
                   er.job_title       AS job_title,
                   er.business_unit   AS business_unit
            FROM employee_role er
            JOIN latest_profiles lp
              ON UPPER(TRIM(lp.employee_id)) = UPPER(TRIM(er.employee_id))
            WHERE lp.rn = 1
            """
        )
    except Exception as exc:
        logger.warning("specialization_landscape: employee_role join failed: %s", exc)
        _specialization_computing = False
        return _default_specialization_landscape()

    if not rows:
        _specialization_computing = False
        return _default_specialization_landscape()

    uid_group: dict = {}
    all_uid_list: list = []
    for r in rows:
        uid = r["user_id"]
        if uid is None or uid in uid_group:
            continue
        uid_group[uid] = classify_role(
            r.get("department_name"), r.get("designation_title"),
            r.get("known_as_name"), r.get("job_title"), r.get("business_unit"),
        )
        all_uid_list.append(uid)

    # AI scores: warm classify_{uid} cache first, batch-compute the uncached —
    # identical path to _compute_proficiency_by_vertical.
    uid_ai = _batch_ai_scores(all_uid_list, "specialization_landscape")

    threshold = settings.ai_proficiency_min_score
    proficient: dict = {}
    for uid in all_uid_list:
        if uid_ai.get(uid, 0.0) >= threshold:
            g = uid_group[uid]
            proficient[g] = proficient.get(g, 0) + 1

    total = sum(proficient.values())
    tracks = [
        {
            "track":   GROUP_LABELS[g],
            "pct":     round(proficient[g] / total * 100, 1) if total else 0.0,
            "earners": proficient[g],
            "col":     GROUP_COLORS[g],
        }
        for g in GROUP_ORDER if proficient.get(g, 0) > 0
    ]
    tracks.sort(key=lambda t: t["pct"], reverse=True)

    result = {"tracks": tracks}
    set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
    logger.info(
        "specialization_landscape: complete — %d AI-proficient across %d groups",
        total, len(tracks),
    )
    _specialization_computing = False
    return result


def _default_specialization_landscape() -> dict:
    """Empty placeholder shown while the background job computes."""
    return {"tracks": []}


_team_quadrant_computing = False  # guard against concurrent recomputes


def _compute_team_quadrant() -> dict:
    """
    4-quadrant "Team Landscape" scatter: each point is a manager's team, placed
    by team-average AI proficiency (y, 0-100) and team-average ALL-TIME active
    days (x). Teams of the same continent (of the manager) that land close
    together are clustered into one bigger dot.

    Returns {"points": [{id, x, y, continent, teams, people, managers:[{id,name}]}],
             "maxX", "maxY"} — axes auto-scale to max+10. Cached 25 h under
    "team_quadrant".
    """
    global _team_quadrant_computing
    from nova_db.gpt_cache import get_cache, set_cache
    from core.queries import _DEDUP_CTE
    from core.geo import continent_for
    from collections import defaultdict

    CACHE_KEY = "team_quadrant"
    cached = get_cache(CACHE_KEY)
    if cached:
        _team_quadrant_computing = False
        return cached["result"]

    _team_quadrant_computing = True
    logger.info("team_quadrant: computing per-team AI-vs-activity landscape")

    try:
        emp_rows = _query(
            _DEDUP_CTE + """
            SELECT user_id, manager, country_code, display_name
            FROM latest_profiles
            WHERE rn=1 AND user_id IS NOT NULL
            """
        )
    except Exception as exc:
        logger.warning("team_quadrant: employee query failed: %s", exc)
        _team_quadrant_computing = False
        return _default_team_quadrant()

    if not emp_rows:
        _team_quadrant_computing = False
        return _default_team_quadrant()

    uid_cc: dict = {}
    uid_name: dict = {}
    mgr_reports: dict = defaultdict(list)
    all_uids: set = set()
    for r in emp_rows:
        uid = r["user_id"]
        if uid is None:
            continue
        uid_cc[uid] = r.get("country_code")
        uid_name[uid] = (r.get("display_name") or "").strip().title() or f"User {uid}"
        all_uids.add(uid)
        if r.get("manager") is not None:
            mgr_reports[r["manager"]].append(uid)

    # All-time distinct active days per uid (3-source union, no date cap).
    # NOTE: SQLite has no DATE type — use date(col), NOT CAST(col AS DATE) (which
    # collapses to numeric affinity and garbles the count). Mirrors _build_people_list.
    uid_days: dict = {}
    try:
        ad_rows = _query(
            """
            SELECT user_id, COUNT(DISTINCT activity_date) AS days FROM (
                SELECT user_id, date(credit_date) AS activity_date
                FROM fact_classmate_learning_credit WHERE is_deleted=0 AND duration>0
                UNION
                SELECT user_id, date(modified_on)
                FROM fact_classmate_user_skill_status WHERE is_deleted=0 AND is_active=1
                UNION
                SELECT user_id, date(attended_date)
                FROM fact_classmate_self_study WHERE status=2 AND is_deleted=0
            ) s WHERE activity_date IS NOT NULL GROUP BY user_id
            """
        )
        uid_days = {r["user_id"]: int(r["days"] or 0) for r in ad_rows}
    except Exception as exc:
        logger.warning("team_quadrant: active-days query failed: %s", exc)

    # AI score per uid — warm classify_{uid} cache first, batch-compute the rest.
    uid_ai = _batch_ai_scores(all_uids, "team_quadrant")

    # Per-team aggregation (drop teams whose manager has no mapped continent).
    teams = []
    for mgr, reps in mgr_reports.items():
        if not reps:
            continue
        cont = continent_for(uid_cc.get(mgr))
        if cont is None:
            continue
        avg_ai = sum(uid_ai.get(u, 0.0) for u in reps) / len(reps)
        avg_days = sum(uid_days.get(u, 0) for u in reps) / len(reps)
        teams.append({
            "mgr_id":   mgr,
            "name":     uid_name.get(mgr, f"User {mgr}"),
            "size":     len(reps),
            "ai":       avg_ai,
            "days":     avg_days,
            "cont":     cont,
        })

    if not teams:
        _team_quadrant_computing = False
        return _default_team_quadrant()

    max_ai = max(t["ai"] for t in teams)
    max_days = max(t["days"] for t in teams)
    axis_y = min(100, int(max_ai) + 1) + 10
    axis_x = int(max_days) + 1 + 10

    # One point per team (no clustering) so each dot is a single manager's team.
    points = [
        {
            "id":         str(t["mgr_id"]),
            "manager_id": t["mgr_id"],
            "name":       t["name"],
            "x":          round(t["days"], 2),
            "y":          round(t["ai"], 1),
            "continent":  t["cont"],
            "people":     t["size"],
        }
        for t in teams
    ]

    result = {"points": points, "maxX": axis_x, "maxY": axis_y}
    set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
    logger.info(
        "team_quadrant: complete — %d teams (maxX=%d maxY=%d)",
        len(points), axis_x, axis_y,
    )
    _team_quadrant_computing = False
    return result


def _default_team_quadrant() -> dict:
    """Empty placeholder shown while the background job computes."""
    return {"points": [], "maxX": 10, "maxY": 100}



# ── Search helpers ────────────────────────────────────────────────────────────

def _fuzzy_filter(rows: list, q: str) -> list:
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


def _search_recursive_org(manager_id: int, q: str) -> list:
    """Search everyone in a manager's org subtree — direct AND indirect reports,
    walking the `manager` field transitively. Derived from _DEDUP_CTE (promoted to
    WITH RECURSIVE) so the active/non-TMP dedup filter stays identical; an `org`
    CTE seeds from manager_id's direct reports and recurses down. UNION (not UNION ALL)
    dedupes visited user_ids, so a cyclic manager-chain in the source can't loop
    forever — the walk is bounded by the number of distinct people."""
    from core.queries import _DEDUP_CTE, _title_case_fields
    cte = _DEDUP_CTE.replace("WITH latest_profiles", "WITH RECURSIVE latest_profiles", 1)
    rows = _query(
        cte + """,
        org(user_id) AS (
            SELECT user_id FROM latest_profiles WHERE rn = 1 AND manager = ?
            UNION
            SELECT ep.user_id
            FROM   latest_profiles ep
            JOIN   org ON ep.manager = org.user_id
            WHERE  ep.rn = 1
        )
        SELECT ep.user_id,
            LOWER(TRIM(ep.display_name))     AS name,
            LOWER(TRIM(ep.department_code))  AS department,
            LOWER(TRIM(ep.designation_code)) AS designation
        FROM latest_profiles ep
        JOIN org ON org.user_id = ep.user_id
        WHERE ep.rn = 1
        ORDER BY ep.display_name
        """,
        (manager_id,),
    )
    _title_case_fields(rows)
    return _fuzzy_filter(rows, q)


def _search_company_wide(q: str) -> list:
    from core.queries import _DEDUP_CTE, _title_case_fields
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
    _title_case_fields(rows)
    return _fuzzy_filter(rows, q)


def _enrich_search_results(uids: list, rows: list) -> list:
    if not uids:
        return []
    # Placeholder count for IN(...), not a value — each id is still bound
    # through the parameterised '?' slots below, never concatenated.
    placeholders = ",".join("?" * len(uids))
    uid_to_row = {r["user_id"]: r for r in rows}

    uid_credits: dict = {}
    try:
        credit_rows = _query(
            f"""SELECT user_id, SUM(learning_credits) AS credits
                FROM vw_classmate_trainings
                WHERE user_id IN ({placeholders})
                  AND status=4052
                  AND completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-90 days')
                GROUP BY user_id""",
            tuple(uids),
        )
        uid_credits = {r["user_id"]: float(r["credits"] or 0) for r in credit_rows}
    except Exception as exc:
        logger.warning("_enrich_search_results: credits query failed: %s", exc)

    uid_last: dict = {}
    try:
        last_credit_rows = _query(
            f"""SELECT user_id, MAX(credit_date) AS last
                FROM fact_classmate_learning_credit
                WHERE user_id IN ({placeholders}) AND is_deleted=0
                GROUP BY user_id""",
            tuple(uids),
        )
        for r in last_credit_rows:
            d = r["last"]
            if d:
                uid_last[r["user_id"]] = str(d.date() if hasattr(d, "date") else d)
    except Exception as exc:
        logger.warning("_enrich_search_results: last-active query failed: %s", exc)

    uid_ai: dict = {}
    uid_scored_by: dict = {}
    team_norm: dict = {}
    try:
        from services.skill_service import get_team_skill_scores
        team_norm = get_team_skill_scores(uids)
        uid_ai        = {uid: round(team_norm.get(uid, {}).get("AI", 0.0), 1) for uid in uids}
        uid_scored_by = {uid: team_norm.get(uid, {}).get("_scored_by", "keywords") for uid in uids}
    except Exception as exc:
        logger.warning("_enrich_search_results: skill scores failed: %s", exc)

    # Tier via the shared batch helper: reads warm tier_{uid} caches and
    # batch-computes any cold uids in 3 Fabric queries (not 6×N per user).
    uid_tier = _batch_tier_map(uids, team_norm)

    # Per-person current streak (days) for the shared people-row streak pill.
    try:
        from services.streak_service import get_team_streaks
        uid_streaks = get_team_streaks(uids)
    except Exception as exc:
        logger.warning("_enrich_search_results: streaks failed: %s", exc)
        uid_streaks = {}

    result = []
    for uid in uids:
        r = uid_to_row.get(uid, {})
        ai = uid_ai.get(uid, 0.0)
        result.append({
            "user_id":              uid,
            "name":                 r.get("name", ""),
            "department":           r.get("department", "Unknown"),
            "designation":          r.get("designation", ""),
            "tier":                 uid_tier.get(uid, "—"),
            "credits_this_quarter": round(uid_credits.get(uid, 0.0), 1),
            "streak_days":          int(uid_streaks.get(uid, 0)),
            "ai_proficiency":       ai,
            "status":               "at_risk" if ai < 20 else "on_track",
            "last_active":          uid_last.get(uid, "never"),
            "scored_by":            uid_scored_by.get(uid, "keywords"),
        })
    return result


def _fetch_overview_swr_data(manager_id: int) -> dict:
    """All 9 stale-while-revalidate data fetches for /manager/overview (return
    last-cached value instantly, recompute in background so the request never
    blocks on a Fabric scan), plus the manual ai_proficiency_trend cache read
    and the team-leaderboard sort. Mutates the module-level _trend_computing
    guard directly — must stay a mutation here (not a return value) so
    concurrent requests see it immediately and never double-schedule the
    trend recompute."""
    global _trend_computing

    at_risk = _swr(
        f"at_risk_{manager_id}",
        lambda: get_at_risk_employees(manager_id),
        [],
    )
    overview_stats = _swr(
        "company_overview_stats", _compute_company_overview_stats,
        {"headcount": 0, "active_this_week": 0,
         "avg_credits_this_quarter": 0.0},
    )
    retention = _swr(
        "retention_snapshot", _compute_company_retention,
        {"rate": 0.0, "trend_pct": 0.0, "trend_dir": "flat"},
    )
    at_risk_count = _swr(
        "company_at_risk_count", _compute_company_at_risk_count,
        {"count": 0, "trend_pct": 0.0, "trend_dir": "flat"},
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

    # AI proficiency by region (bar-chart tab). Stale-while-revalidate so the
    # request never blocks on a full company scan; empty placeholder until
    # the background compute finishes.
    proficiency_by_region = _swr(
        "ai_proficiency_by_region",
        _compute_ai_proficiency_by_region,
        _default_region_snapshot(),
    )

    # Proficiency by vertical (bar chart) — % AI-proficient per industry group.
    proficiency_by_vertical = _swr(
        "proficiency_by_vertical",
        _compute_proficiency_by_vertical,
        _default_proficiency_by_vertical(),
    )

    # Specialization landscape (stacked bar) — share of AI-proficient people per role group.
    specialization_landscape = _swr(
        "specialization_landscape",
        _compute_specialization_landscape,
        _default_specialization_landscape(),
    )

    # Team landscape (4-quadrant scatter) — each dot a team by avg AI vs avg active days.
    team_quadrant = _swr(
        "team_quadrant",
        _compute_team_quadrant,
        _default_team_quadrant(),
    )

    # Team Leaderboard = per-manager team skill average (name + % only),
    # each team being one manager's direct reports scored on the mean of all
    # 5 skill verticals. Sorted best-first; empty list until the snapshot is warm.
    mgr_snap = _swr("team_leaderboard_by_manager", _compute_manager_team_snapshot, [])
    team_leaderboard = sorted(
        [{"name": m["name"], "office": m.get("office", ""), "prof": m["avg_skill_pct"], "manager_id": m["manager_id"]} for m in mgr_snap],
        key=lambda x: x["prof"], reverse=True,
    )

    return {
        "at_risk": at_risk,
        "overview_stats": overview_stats,
        "retention": retention,
        "at_risk_count": at_risk_count,
        "cached_trend": cached_trend,
        "monthly_trend": monthly_trend,
        "proficiency_by_region": proficiency_by_region,
        "proficiency_by_vertical": proficiency_by_vertical,
        "specialization_landscape": specialization_landscape,
        "team_quadrant": team_quadrant,
        "team_leaderboard": team_leaderboard,
    }


def _derive_ai_proficiency_metrics(cached_trend, headcount: int) -> tuple:
    """Derives (ai_prof_pct, ai_prof_count, ai_trend_pts) from the cached
    quarterly AI-proficiency trend and the current company headcount."""
    if cached_trend and cached_trend["result"]:
        trend_list    = cached_trend["result"]
        ai_prof_pct   = trend_list[-1]["credits"]
        ai_prof_count = round(headcount * ai_prof_pct / 100)
        ai_trend_pts  = (
            round(trend_list[-1]["credits"] - trend_list[-2]["credits"], 1)
            if len(trend_list) >= 2 else 0.0
        )
    else:
        ai_prof_pct   = 0.0
        ai_prof_count = 0
        ai_trend_pts  = 0.0
    return ai_prof_pct, ai_prof_count, ai_trend_pts


def _build_overview_response(data: dict, headcount, active_week,
                              ai_prof_pct, ai_prof_count, ai_trend_pts) -> dict:
    """Assembles the final /manager/overview response dict from the raw SWR
    fetches (`data`, from _fetch_overview_swr_data) and the derived AI
    proficiency metrics."""
    overview_stats = data["overview_stats"]
    retention = data["retention"]
    at_risk_count = data["at_risk_count"]
    return {
        "kpis": {
            "total_team":               headcount,
            "active_this_week":         active_week,
            "ai_proficient_count":      ai_prof_count,
            "ai_proficient_pct":        ai_prof_pct,
            "avg_credits_this_quarter": overview_stats["avg_credits_this_quarter"],
            "retention_rate":           retention["rate"],
            "retention_rate_trend_pct": retention["trend_pct"],
            "retention_rate_trend_dir": retention["trend_dir"],
            "ai_proficiency_trend_pts": ai_trend_pts,
            "at_risk_count_company":    at_risk_count.get("count", 0),
            "at_risk_count_trend_pct":  at_risk_count.get("trend_pct", 0.0),
            "at_risk_count_trend_dir":  at_risk_count.get("trend_dir", "flat"),
        },
        "monthly_trend": data["monthly_trend"],
        "proficiency_by_region": data["proficiency_by_region"],
        "proficiency_by_vertical": data["proficiency_by_vertical"],
        "specialization_landscape": data["specialization_landscape"],
        "team_quadrant": data["team_quadrant"],
        "team_leaderboard": data["team_leaderboard"],
        "at_risk":        data["at_risk"],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/manager/overview")
async def manager_overview(user: CurrentUser = Depends(get_current_user)):
    # Overview is company-wide and restricted to exec managers only.
    if not _is_exec_manager(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Executive manager access required",
        )
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")

    manager_id = user.classmate_user_id
    try:
        data = _fetch_overview_swr_data(manager_id)
        headcount   = data["overview_stats"]["headcount"]
        active_week = data["overview_stats"]["active_this_week"]
        ai_prof_pct, ai_prof_count, ai_trend_pts = _derive_ai_proficiency_metrics(
            data["cached_trend"], headcount)
    except Exception as exc:
        logger.warning("Warehouse unavailable for manager overview uid=%s: %s", manager_id, exc)
        raise HTTPException(status_code=503, detail="Data unavailable")

    return _build_overview_response(data, headcount, active_week, ai_prof_pct, ai_prof_count, ai_trend_pts)


def _batch_tier_map(uids: list, team_norm: dict) -> dict:
    """
    Returns {uid: current_tier_string} for the given uids — a PURE CACHE READ.

    Tiers are computed in one place only (services.tier_service computes & caches
    them via the nightly/startup refresh and populate_missing_tiers). This reads
    the warm tier_{uid} caches; any uids still missing are populated in a single
    batch via the shared helper, so the manager view always matches the employee
    view exactly. `team_norm` is reused as the skill input to avoid re-fetching.
    """
    from nova_db.gpt_cache import get_cache
    from services.tier_service import compute_and_cache_tiers, populate_missing_tiers

    if not uids:
        return {}

    tier_map: dict = {}
    cold: list = []
    for uid in uids:
        _tc = get_cache(f"tier_{uid}")
        if _tc:
            tier_map[uid] = _tc["result"].get("current_tier", "—")
        else:
            cold.append(uid)

    if cold:
        # Populate everything not yet cached in one batch (reusing the skill scores
        # we already have for these uids), then read the requested ones back.
        populate_missing_tiers()
        still_cold = [uid for uid in cold if not get_cache(f"tier_{uid}")]
        if still_cold:
            compute_and_cache_tiers(still_cold, skill_norm=team_norm)
        for uid in cold:
            _tc = get_cache(f"tier_{uid}")
            tier_map[uid] = _tc["result"].get("current_tier", "—") if _tc else "—"

    return tier_map


def _build_people_list(manager_id: int, filter_val: str) -> list:
    from services.skill_service import get_team_skill_scores
    from nova_db.gpt_cache import get_cache, set_cache

    # v4 = payload now also carries designation_title (from employee_role).
    _cache_key = f"people_list_v4_{manager_id}_{filter_val}"
    _cached = get_cache(_cache_key)
    if _cached:
        return _cached["result"]

    reports = get_direct_reports(None, manager_id)
    if not reports:
        return []

    uids = [r["user_id"] for r in reports]
    uid_to_report = {r["user_id"]: r for r in reports}
    # Placeholder count for IN(...), not a value — each id is still bound
    # through the parameterised '?' slots below, never concatenated.
    placeholders = ",".join("?" * len(uids))

    credit_rows = _query(
        f"""
        SELECT user_id, SUM(learning_credits) AS credits
        FROM   vw_classmate_trainings
        WHERE  user_id IN ({placeholders})
          AND  status = 4052
          AND  completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-90 days')
        GROUP BY user_id
        """,
        tuple(uids),
    )
    uid_credits = {r["user_id"]: float(r["credits"] or 0) for r in credit_rows}

    uid_last_active: dict = {}
    try:
        last_rows = _query(
            f"""
            SELECT user_id, MAX(credit_date) AS last_date
            FROM   fact_classmate_learning_credit
            WHERE  user_id IN ({placeholders})
              AND  is_deleted = 0
            GROUP BY user_id
            """,
            tuple(uids),
        )
        uid_last_active = {r["user_id"]: r["last_date"] for r in last_rows}
    except Exception as exc:
        logger.warning("_build_people_list: last-active query failed: %s", exc)

    # All-time distinct active days per person (for the Team Landscape scatter's
    # y-axis). Same 3-source activity union streak_service uses, but with NO date
    # cap (streaks window to 30/90/365 days; this is lifetime). date() the raw
    # timestamps and COUNT(DISTINCT) so multiple same-day events count once.
    uid_active_days: dict = {}
    try:
        ad_rows = _query(
            f"""
            SELECT user_id, COUNT(DISTINCT activity_date) AS days FROM (
                SELECT user_id, date(credit_date) AS activity_date
                FROM   fact_classmate_learning_credit
                WHERE  user_id IN ({placeholders}) AND is_deleted = 0 AND duration > 0
                UNION
                SELECT user_id, date(modified_on)
                FROM   fact_classmate_user_skill_status
                WHERE  user_id IN ({placeholders}) AND is_deleted = 0 AND is_active = 1
                UNION
                SELECT user_id, date(attended_date)
                FROM   fact_classmate_self_study
                WHERE  user_id IN ({placeholders}) AND status = 2 AND is_deleted = 0
            ) src
            WHERE activity_date IS NOT NULL
            GROUP BY user_id
            """,
            tuple(uids) * 3,
        )
        uid_active_days = {r["user_id"]: int(r["days"] or 0) for r in ad_rows}
    except Exception as exc:
        logger.warning("_build_people_list: active-days query failed: %s", exc)

    # Designation title from employee_role (joined by normalized employee_id) —
    # the human-readable job title shown under each name in Individual Progress.
    uid_designation_title: dict = {}
    try:
        emp_id_to_uid = {
            str(r["employee_id"]).strip().upper(): r["user_id"]
            for r in reports
            if r.get("employee_id")
        }
        if emp_id_to_uid:
            role_placeholders = ",".join("?" * len(emp_id_to_uid))
            role_rows = _query(
                f"""
                SELECT UPPER(TRIM(employee_id)) AS emp_id, designation_title
                FROM   employee_role
                WHERE  UPPER(TRIM(employee_id)) IN ({role_placeholders})
                """,
                tuple(emp_id_to_uid.keys()),
            )
            for r in role_rows:
                uid = emp_id_to_uid.get(r["emp_id"])
                if uid is not None and r["designation_title"]:
                    uid_designation_title[uid] = str(r["designation_title"]).strip()
    except Exception as exc:
        logger.warning("_build_people_list: designation_title query failed: %s", exc)

    try:
        team_norm = get_team_skill_scores(uids)
    except Exception as exc:
        logger.warning("get_team_skill_scores failed: %s", exc)
        team_norm = {}

    # Per-person current streak (days) — reads warm streak_{uid} caches,
    # computes any misses per-user.
    try:
        from services.streak_service import get_team_streaks
        uid_streaks = get_team_streaks(uids)
    except Exception as exc:
        logger.warning("get_team_streaks failed: %s", exc)
        uid_streaks = {}

    # Batch tier lookup — reads warm tier_{uid} caches and batch-computes any
    # cold uids in 3 Fabric queries (see _batch_tier_map), faithfully matching
    # calculate_tier() so the manager view matches the employee view.
    tier_map = _batch_tier_map(uids, team_norm)

    employees = []
    for uid in uids:
        r = uid_to_report[uid]
        last = uid_last_active.get(uid)
        if last:
            last_date = last.date() if hasattr(last, "date") else last
            last_active_str = str(last_date)
        else:
            last_active_str = "never"

        emp_credits = uid_credits.get(uid, 0.0)

        ai_proficiency = round(team_norm.get(uid, {}).get("AI", 0.0), 1)
        scored_by      = team_norm.get(uid, {}).get("_scored_by", "keywords")

        # Status is purely AI-proficiency based: < 20% = at risk, otherwise on track.
        emp_status = "at_risk" if ai_proficiency < 20 else "on_track"

        if filter_val == "on_track" and emp_status != "on_track":
            continue
        if filter_val == "at_risk" and emp_status != "at_risk":
            continue

        emp_tier = tier_map.get(uid, "—")

        employees.append({
            "user_id":              uid,
            "name":                 r["name"],
            "department":           r["department"] or "Unknown",
            "designation":          r.get("designation", ""),
            "designation_title":    uid_designation_title.get(uid) or "",
            "tier":                 emp_tier,
            "credits_this_quarter": round(emp_credits, 1),
            "streak_days":          int(uid_streaks.get(uid, 0)),
            "active_days_total":    int(uid_active_days.get(uid, 0)),
            "ai_proficiency":       ai_proficiency,
            "status":               emp_status,
            "last_active":          last_active_str,
            "scored_by":            scored_by,
        })

    set_cache(_cache_key, employees, "computed", ttl_hours=25)
    return employees


def _overlay_current_tiers(employees: list) -> list:
    """Overlay each employee's CURRENT tier from the authoritative tier_{uid}
    store (kept in sync with user_tier_scores by the nightly/startup batch).

    The people_list_{mgr}_* cache can be older than the last tier refresh, so we
    never trust its baked-in tier — we always pull the live one. This is what
    keeps the manager view in lock-step with the employee dashboard."""
    from nova_db.gpt_cache import get_cache
    from services.tier_service import compute_and_cache_tiers

    if not employees:
        return employees

    missing = [e["user_id"] for e in employees if not get_cache(f"tier_{e['user_id']}")]
    if missing:
        compute_and_cache_tiers(missing)

    for e in employees:
        tc = get_cache(f"tier_{e['user_id']}")
        if tc:
            e["tier"] = tc["result"].get("current_tier", e.get("tier", "—"))
    return employees


def _compute_your_team(manager_id: int) -> dict:
    """Team radar + badge summary for a manager's direct reports. Writes its own
    cache (key your_team_v3_{manager_id}) so it can be served via _swr."""
    from services.skill_service import get_team_skill_radar
    from nova_db.badges import get_team_badge_summary
    from nova_db.gpt_cache import set_cache

    reports = get_direct_reports(None, manager_id)
    uids = [r["user_id"] for r in reports]
    n = len(uids)

    radar = get_team_skill_radar(uids)

    badge_summary = get_team_badge_summary(uids)
    badge_summary["avg_per_person"] = round(badge_summary["total"] / n, 1) if n else 0.0

    result = {
        "team_size": n,
        "radar": radar,
        "badges": badge_summary,
        "active_this_week": _get_team_active_this_week(uids),
        "courses_this_week": _get_team_courses_completed_this_week(uids),
        "top_teams": _get_top_teams_with_radar(),
    }
    set_cache(f"your_team_v3_{manager_id}", result, "computed", ttl_hours=25)
    return result


@router.get("/manager/your-team")
async def manager_your_team(
    filter: str = Query("all", pattern="^(all|on_track|at_risk)$"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Direct-reports-only team view — available to ANY manager (no exec gate).
    Returns the enriched people list (with streak_days), a team-averaged skill
    radar (this vs last month), and a team badge summary.
    """
    _require_manager(user)
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")

    manager_id = user.classmate_user_id
    try:
        employees = await _run(_build_people_list, manager_id, filter)
        employees = await _run(_overlay_current_tiers, employees)
        extras    = _swr(f"your_team_v3_{manager_id}", partial(_compute_your_team, manager_id),
                         {"team_size": 0, "active_this_week": 0, "courses_this_week": 0,
                          "top_teams": [],
                          "radar": {"axes": ["AI", "Cloud", "Frontend", "Backend", "Data"],
                                    "this_month": [0.0] * 5, "last_month": [0.0] * 5},
                          "badges": {"total": 0, "avg_per_person": 0.0, "this_month_count": 0,
                                     "by_tier": {"platinum": 0, "diamond": 0, "gold": 0,
                                                 "silver": 0, "bronze": 0}}})
    except Exception as exc:
        logger.warning("Warehouse unavailable for your-team uid=%s: %s", manager_id, exc)
        raise HTTPException(status_code=503, detail="Data unavailable")

    return {
        "employees":         employees,
        "radar":             extras["radar"],
        "badges":            extras["badges"],
        "active_this_week":  extras.get("active_this_week", 0),
        "courses_this_week": extras.get("courses_this_week", 0),
        "team_size":         extras.get("team_size", len(employees)),
        "top_teams":         extras.get("top_teams", []),
    }


@router.get("/manager/people/search")
async def manager_people_search(
    q: str = Query("", min_length=0, max_length=100),
    user: CurrentUser = Depends(get_current_user),
):
    """The manager page "Individual Progress" search, keyed off the signed-in
    user's profile: exec profiles get company-wide results, every other manager
    gets their own org subtree (direct + indirect reports)."""
    if not q or not q.strip():
        return {"employees": [], "search_scope": "none"}
    q_lower = q.strip().lower()

    eff_uid = user.classmate_user_id
    if eff_uid is None:
        return {"employees": [], "search_scope": "dev"}
    if eff_uid not in EXEC_USER_IDS:
        _require_manager(user)

    try:
        if eff_uid in EXEC_USER_IDS:        # exec profile → whole company
            rows  = await _run(_search_company_wide, q_lower)
            scope_out = "company"
        else:                               # any other manager → their org subtree
            rows  = await _run(_search_recursive_org, eff_uid, q_lower)
            scope_out = "recursive"

        uids     = [r["user_id"] for r in rows]
        enriched = await _run(_enrich_search_results, uids, rows)

    except Exception as exc:
        logger.warning("Search failed uid=%s q=%s: %s", eff_uid, _safe_log(q), exc)
        return {"employees": [], "search_scope": "error"}

    return {"employees": enriched, "search_scope": scope_out}
