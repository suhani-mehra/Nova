"""
routers/manager.py
Manager-only endpoints: /api/manager/overview, /api/manager/teams,
/api/manager/people, /api/manager/people/search.
"""

import asyncio
import logging
import math
import zlib as _zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.auth import CurrentUser, get_current_user
from core.database import query as _query
from core.config import settings
from core.queries import get_direct_reports
from services.team_service import get_at_risk_employees

logger = logging.getLogger(__name__)
router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=8)

_trend_computing = False          # guard against concurrent AI trend recomputes
_dept_snapshot_computing = False  # guard against concurrent dept snapshot recomputes
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

EXEC_USER_IDS: set[int] = {5575, 16467, 16465, 16470}  # hardcoded + DB-resolved
EXEC_USER_NAMES = [
    "suhani mehra",
    "niva nimesh shah",
    "eric verdes",
]
RECURSIVE_USER_IDS: set[int] = {5575}


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
        ph = ",".join("?" * len(chunk))
        try:
            activity_rows = _query(
                f"""
                SELECT DISTINCT user_id, CAST(activity_date AS DATE) AS activity_date
                FROM (
                    SELECT user_id, credit_date AS activity_date
                    FROM classmate.fact_classmate_learning_credit
                    WHERE user_id IN ({ph}) AND is_deleted=0 AND duration>0
                    UNION
                    SELECT user_id, CAST(modified_on AS DATE)
                    FROM classmate.fact_classmate_user_skill_status
                    WHERE user_id IN ({ph}) AND is_deleted=0 AND is_active=1
                    UNION
                    SELECT user_id, attended_date
                    FROM classmate.fact_classmate_self_study
                    WHERE user_id IN ({ph}) AND status=2 AND is_deleted=0
                ) src
                WHERE activity_date IS NOT NULL
                  AND activity_date >= CAST(DATEADD(day,-365,GETDATE()) AS DATE)
                """,
                tuple(chunk) * 3,
            )
            week_rows = _query(
                f"""
                SELECT user_id, SUM(duration) AS total_dur
                FROM classmate.fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0 AND duration>0
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


def _prewarm_tier_cache():
    """
    Background job: write a preliminary tier_{uid} for every user in
    user_tier_scores whose cache is cold. Uses the batch-computed tier_score
    (global-avg recency) with a short 2h TTL so it's replaced by the exact
    per-user value the first time calculate_tier() runs for real.
    """
    from nova_db.tier_scores import get_all_tier_scores
    from nova_db.gpt_cache import get_cache, set_cache
    from services.tier_service import _percentile_to_tier

    all_scores = get_all_tier_scores()
    if not all_scores:
        logger.info("tier pre-warm: no scores in user_tier_scores, skipping")
        return

    sorted_scores = sorted(all_scores.values(), reverse=True)
    total_pop     = len(sorted_scores)
    cold          = [(uid, score) for uid, score in all_scores.items()
                     if not get_cache(f"tier_{uid}")]
    logger.info("tier pre-warm: %d cold out of %d total", len(cold), total_pop)

    for uid, tier_score in cold:
        rank       = sum(1 for s in sorted_scores if s > tier_score)
        approx_pct = rank / total_pop * 100
        current_tier, next_tier = _percentile_to_tier(approx_pct)
        set_cache(f"tier_{uid}", {
            "current_tier":      current_tier,
            "next_tier":         next_tier,
            "tier_progress":     0,
            "percentile":        round(approx_pct, 1),
            "total_credits":     0.0,
            "tier_score":        round(tier_score, 1),
            "credits_score":     0.0,
            "skill_score":       0.0,
            "consistency_score": 0.0,
            "recency_score":     0.0,
            "scored_by":         "batch",
        }, "computed", ttl_hours=25)

    logger.info("tier pre-warm complete: %d entries written", len(cold))


def _prewarm_manager_people_cache(chunk_size: int = 20):
    """
    Pre-warm people_list_{mgr_id}_all for every active manager.
    Called at 3 AM after classify/streak/tier caches are warm,
    so _build_people_list() runs cheaply (no per-user Fabric queries).
    Also populates direct_reports_{mgr_id} as a side effect.
    """
    mgr_rows = _query("""
        SELECT DISTINCT manager AS mgr_id
        FROM classmate.dim_classmate_employee_profile
        WHERE is_deleted = 0 AND manager IS NOT NULL
    """)
    if not mgr_rows:
        logger.info("manager people pre-warm: no managers found")
        return

    mgr_ids = [r["mgr_id"] for r in mgr_rows if r["mgr_id"]]
    logger.info("manager people pre-warm: %d managers", len(mgr_ids))

    for i in range(0, len(mgr_ids), chunk_size):
        chunk = mgr_ids[i: i + chunk_size]
        for mgr_id in chunk:
            try:
                _build_people_list(mgr_id, "all")
            except Exception as exc:
                logger.warning("manager people pre-warm failed for mgr=%s: %s", mgr_id, exc)
        logger.info(
            "manager people pre-warm: %d/%d done",
            min(i + chunk_size, len(mgr_ids)),
            len(mgr_ids),
        )

    logger.info("manager people pre-warm complete")


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
        "retention_rate":           0.0,
        "retention_rate_trend_pct": 0.0,
        "retention_rate_trend_dir": "flat",
        "ai_proficiency_trend_pts": 0.0,
        "active_week_trend_pct":    0.0,
        "at_risk_count_company":    0,
        "at_risk_count_trend_pct":  0.0,
        "at_risk_count_trend_dir":  "flat",
    },
    "monthly_trend": [
        {"month": "Q1 '25", "credits": 0.0, "active_pct": 0.0},
        {"month": "Q2 '25", "credits": 0.0, "active_pct": 0.0},
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
            "trend_pct":         0.0,
            "top_course":        "Python for Data Science",
            "tier_distribution": {
                "platinum": 0, "diamond": 1, "gold": 2,
                "silver": 1, "bronze": 1, "starter": 0,
            },
        }
    ]
}

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
        FROM classmate.dim_classmate_employee_profile
        WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
    """)
    return int(rows[0]["n"] or 0) if rows else 0


def _get_company_active_this_week() -> int:
    """
    Active learners this week = employees with at least one active day in the
    current week, read from the streak cache (week_map). This matches how a
    user's streak is defined (3-source activity union) rather than only counting
    when learning credits happen to be awarded.
    """
    from nova_db.gpt_cache import get_cache
    count = 0
    for uid in _get_all_active_uids():
        c = get_cache(f"streak_{uid}")
        if c and any(c["result"].get("week_map") or []):
            count += 1
    return count


def _get_company_avg_credits_this_quarter() -> float:
    rows = _query("""
        SELECT AVG(s.credits) AS avg_c FROM (
            SELECT user_id, SUM(learning_credits) AS credits
            FROM classmate.vw_classmate_trainings
            WHERE status=4052 AND completed_on >= DATEADD(day,-90,GETDATE())
              AND user_id IN (
                  SELECT DISTINCT user_id
                  FROM classmate.dim_classmate_employee_profile
                  WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
              )
            GROUP BY user_id
        ) s
    """)
    return round(float(rows[0]["avg_c"] or 0), 1) if rows else 0.0


def _compute_company_overview_stats() -> dict:
    """
    Bundles the company-wide overview metrics into one cached call.

    The "active learners this week" trend compares the current streak-based count
    against a weekly baseline (company_active_prev) — the same pattern used by the
    at-risk count — since the streak cache only knows about the current week and
    can't be diffed against a prior week directly.
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

        # Active-learners trend vs a weekly baseline (only set when absent; it
        # expires after 7 days and the next run re-baselines).
        prev_snap = get_cache("company_active_prev")
        if prev_snap is not None:
            prev_count = int(prev_snap["result"])
            trend_pct  = round((active_week - prev_count) / max(prev_count, 1) * 100, 1)
            trend_dir  = "up" if trend_pct > 0 else "down" if trend_pct < 0 else "flat"
        else:
            trend_pct = 0.0
            trend_dir = "flat"
            set_cache("company_active_prev", active_week, "computed", ttl_hours=24 * 7)

        result = {
            "headcount":                headcount,
            "active_this_week":         active_week,
            "active_week_trend_pct":    trend_pct,
            "active_week_trend_dir":    trend_dir,
            "avg_credits_this_quarter": avg_credits,
        }
        set_cache(CACHE_KEY, result, "computed", ttl_hours=25)
        return result
    except Exception as exc:
        logger.warning("_compute_company_overview_stats failed: %s", exc)
        return {
            "headcount": 0, "active_this_week": 0,
            "active_week_trend_pct": 0.0, "active_week_trend_dir": "flat",
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
            FROM classmate.dim_classmate_employee_profile
            WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
        """)
        all_uids = {r["user_id"] for r in emp_rows if r["user_id"]}
        headcount = len(all_uids)

        rows = _query("""
            SELECT DISTINCT user_id,
                CASE
                    WHEN credit_date >= DATEADD(day,-30,GETDATE())
                    THEN 'cur'
                    WHEN credit_date >= DATEADD(day,-60,GETDATE())
                         AND credit_date < DATEADD(day,-30,GETDATE())
                    THEN 'prev'
                END AS window
            FROM classmate.fact_classmate_learning_credit
            WHERE is_deleted=0
              AND credit_date >= DATEADD(day,-60,GETDATE())
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
    from services.skill_service import MASTERY_THRESHOLD

    CACHE_KEY = "ai_proficiency_trend"
    cached = get_cache(CACHE_KEY)
    if cached:
        _trend_computing = False
        return cached["result"]

    _trend_computing = True
    logger.info("ai_proficiency_trend: computing quarterly trend (company-wide)")

    emp_rows = _query("""
        SELECT DISTINCT user_id
        FROM classmate.dim_classmate_employee_profile
        WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
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
                   fc.completion_date AS completed_on
            FROM classmate.fact_classmate_certification fc
            WHERE fc.status=2 AND fc.is_active=1 AND fc.is_deleted=0
              AND fc.user_id IS NOT NULL AND fc.certificate_id IS NOT NULL
              AND fc.completion_date IS NOT NULL
        """)
    except Exception as exc:
        logger.warning("ai_proficiency_trend: certs query failed: %s", exc)
        cert_rows = []

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
            if min(100.0, math.sqrt(
                sum(v for d, v in user_ai[uid] if d <= cutoff) / MASTERY_THRESHOLD
            ) * 100) >= 45.0
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


def _compute_dept_snapshot() -> list:
    """
    Computes per-department AI proficiency % for all active employees.
    Cached 24 h under key "dept_snapshot". Runs at startup.
    """
    global _dept_snapshot_computing
    from nova_db.gpt_cache import get_cache, set_cache
    from core.queries import _DEDUP_CTE

    CACHE_KEY = "dept_snapshot"
    cached = get_cache(CACHE_KEY)
    if cached:
        _dept_snapshot_computing = False
        return cached["result"]

    _dept_snapshot_computing = True
    logger.info("dept_snapshot: computing company-wide department AI proficiency")

    try:
        emp_rows = _query(
            _DEDUP_CTE + """
            SELECT user_id, LOWER(TRIM(department_code)) AS dept
            FROM latest_profiles
            WHERE rn=1 AND user_id IS NOT NULL AND department_code IS NOT NULL
            """
        )
    except Exception as exc:
        logger.warning("dept_snapshot: employee query failed: %s", exc)
        _dept_snapshot_computing = False
        return []

    if not emp_rows:
        _dept_snapshot_computing = False
        return []

    dept_uids: dict = defaultdict(list)
    all_uid_list: list = []
    for r in emp_rows:
        dept_uids[r["dept"]].append(r["user_id"])
        all_uid_list.append(r["user_id"])

    # Read AI scores from gpt_cache first, then batch-compute uncached
    uid_ai: dict = {}
    uncached_uids: list = []
    for uid in all_uid_list:
        c = get_cache(f"classify_{uid}")
        if c:
            res = c.get("result", {})
            axes = res.get("axes", ["AI", "Cloud", "Frontend", "Backend", "Data"])
            this_month = res.get("this_month", [])
            try:
                ai_idx = axes.index("AI")
                uid_ai[uid] = float(this_month[ai_idx]) if ai_idx < len(this_month) else 0.0
            except (ValueError, IndexError):
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
            logger.warning("dept_snapshot: skill scores failed for uncached batch: %s", exc)
            for uid in uncached_uids:
                uid_ai.setdefault(uid, 0.0)

    threshold = settings.ai_proficiency_min_score

    # Load previous snapshot for trend (stored separately, kept 7 days)
    prev_map: dict = {}
    prev_snap = get_cache("dept_snapshot_prev")
    if prev_snap:
        for d in (prev_snap.get("result") or []):
            prev_map[d["dept"]] = d["ai_proficient_pct"]

    result = []
    for dept, uids_in_dept in dept_uids.items():
        proficient = sum(1 for uid in uids_in_dept if uid_ai.get(uid, 0) >= threshold)
        pct = round(proficient / len(uids_in_dept) * 100, 1) if uids_in_dept else 0.0
        prev_pct = prev_map.get(dept, pct)   # no history → trend 0
        result.append({
            "dept":              dept,
            "headcount":         len(uids_in_dept),
            "ai_proficient_pct": pct,
            "trend_pct":         round(pct - prev_pct, 1),
        })

    result.sort(key=lambda x: x["ai_proficient_pct"], reverse=True)

    # Keep current as "prev" so the next recompute can show a delta
    set_cache("dept_snapshot_prev", result, "computed", ttl_hours=24 * 7)
    set_cache(CACHE_KEY, result, "computed", ttl_hours=25)

    logger.info("dept_snapshot: complete — %d departments", len(result))
    _dept_snapshot_computing = False
    return result


# ── Legacy direct-report helper ───────────────────────────────────────────────

def _avg_credits_this_quarter(uids: list) -> float:
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


def _search_direct_reports(mgr_id: int, q: str) -> list:
    reports = get_direct_reports(None, mgr_id)
    return _fuzzy_filter(reports, q)


def _search_recursive(mgr_id: int, q: str) -> list:
    # Synapse SQL does not support recursive CTEs — fall back to company-wide search.
    return _search_company_wide(q)


def _search_company_wide(q: str) -> list:
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


def _enrich_search_results(uids: list, rows: list) -> list:
    if not uids:
        return []
    ph = ",".join("?" * len(uids))
    uid_to_row = {r["user_id"]: r for r in rows}

    uid_credits: dict = {}
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

    uid_last: dict = {}
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

    result = []
    for uid in uids:
        r = uid_to_row.get(uid, {})
        result.append({
            "user_id":              uid,
            "name":                 r.get("name", ""),
            "department":           r.get("department", "Unknown"),
            "designation":          r.get("designation", ""),
            "tier":                 uid_tier.get(uid, "—"),
            "credits_this_quarter": round(uid_credits.get(uid, 0.0), 1),
            "streak":               0,
            "ai_proficiency":       uid_ai.get(uid, 0.0),
            "status":               "—",
            "last_active":          uid_last.get(uid, "never"),
            "scored_by":            uid_scored_by.get(uid, "keywords"),
        })
    return result


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/manager/overview")
async def manager_overview(user: CurrentUser = Depends(get_current_user)):
    _require_manager(user)
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")

    global _trend_computing
    mgr_id = user.classmate_user_id
    try:
        # All four stats use stale-while-revalidate: return last cached value instantly
        # and recompute in background so the request never blocks on a Fabric scan.
        at_risk = _swr(
            f"at_risk_{mgr_id}",
            lambda: get_at_risk_employees(mgr_id),
            [],
        )
        overview_stats = _swr(
            "company_overview_stats", _compute_company_overview_stats,
            {"headcount": 0, "active_this_week": 0,
             "active_week_trend_pct": 0.0, "active_week_trend_dir": "flat",
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

        headcount   = overview_stats["headcount"]
        active_week = overview_stats["active_this_week"]

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

        active_trend_pct = overview_stats.get("active_week_trend_pct", 0.0)
        active_trend_dir = overview_stats.get("active_week_trend_dir", "flat")

        at_risk_count_num  = at_risk_count.get("count", 0)
        at_risk_trend_pct  = at_risk_count.get("trend_pct", 0.0)
        at_risk_trend_dir  = at_risk_count.get("trend_dir", "flat")

    except Exception as exc:
        logger.warning("Fabric unavailable for manager overview uid=%s: %s", mgr_id, exc)
        raise HTTPException(status_code=503, detail="Data unavailable")

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
            "active_week_trend_pct":    active_trend_pct,
            "active_week_trend_dir":    active_trend_dir,
            "at_risk_count_company":    at_risk_count_num,
            "at_risk_count_trend_pct":  at_risk_trend_pct,
            "at_risk_count_trend_dir":  at_risk_trend_dir,
        },
        "monthly_trend": monthly_trend,
        "at_risk":        at_risk,
    }


@router.get("/manager/teams")
async def manager_teams(user: CurrentUser = Depends(get_current_user)):
    _require_manager(user)
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")

    global _dept_snapshot_computing
    try:
        from nova_db.gpt_cache import get_cache
        cached = get_cache("dept_snapshot")

        if not cached:
            if not _dept_snapshot_computing:
                _dept_snapshot_computing = True
                loop = asyncio.get_event_loop()
                loop.run_in_executor(_executor, _compute_dept_snapshot)
            return {"departments": []}

        raw_depts = cached["result"]
        tier_keys = ["platinum", "diamond", "gold", "silver", "bronze", "starter"]
        departments = [
            {
                "name":              d["dept"],
                "headcount":         d["headcount"],
                "avg_credits":       0.0,
                "ai_proficient_pct": d["ai_proficient_pct"],
                "trend_pct":         d.get("trend_pct", 0.0),
                "top_course":        "N/A",
                "tier_distribution": {k: 0 for k in tier_keys},
            }
            for d in raw_depts
        ]

    except Exception as exc:
        logger.warning("Fabric unavailable for manager teams: %s", exc)
        raise HTTPException(status_code=503, detail="Data unavailable")

    return {"departments": departments}


def _batch_tier_map(uids: list, team_norm: dict) -> dict:
    """
    Returns {uid: current_tier_string} for the given uids.

    Reads warm tier_{uid} caches; for cold uids, batch-computes the tier with
    the same formula as calculate_tier() using just 3 Fabric queries total
    (not 6×N), then writes the full tier_{uid} entry (25h TTL) so the employee
    view and people tab stay consistent. `team_norm` is the output of
    get_team_skill_scores(uids).
    """
    from nova_db.gpt_cache import get_cache, set_cache
    from nova_db.tier_scores import get_all_tier_scores
    from services.tier_service import _percentile_to_tier

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

    if not cold:
        return tier_map

    ph = ",".join("?" * len(cold))
    uid_alltime_credits: dict = {}
    uid_recency_30d: dict = {}
    uid_active_days_90: dict = {}
    try:
        rows = _query(
            f"SELECT user_id, ISNULL(SUM(learning_credits),0) AS tc "
            f"FROM classmate.vw_classmate_trainings "
            f"WHERE user_id IN ({ph}) AND status=4052 GROUP BY user_id",
            tuple(cold),
        )
        uid_alltime_credits = {r["user_id"]: float(r["tc"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("_batch_tier_map alltime credits failed: %s", exc)
    try:
        rows = _query(
            f"SELECT user_id, ISNULL(SUM(value),0) AS c30 "
            f"FROM classmate.fact_classmate_learning_credit "
            f"WHERE user_id IN ({ph}) AND is_deleted=0 "
            f"  AND credit_date >= DATEADD(day,-30,GETDATE()) GROUP BY user_id",
            tuple(cold),
        )
        uid_recency_30d = {r["user_id"]: float(r["c30"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("_batch_tier_map recency failed: %s", exc)
    try:
        # 90-day window ending today inclusive — matches calculate_streak's count.
        rows = _query(
            f"""SELECT user_id, COUNT(DISTINCT activity_date) AS ad90
            FROM (
                SELECT user_id, CAST(credit_date AS DATE) AS activity_date
                FROM classmate.fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0 AND duration>0
                UNION
                SELECT user_id, CAST(modified_on AS DATE)
                FROM classmate.fact_classmate_user_skill_status
                WHERE user_id IN ({ph}) AND is_deleted=0 AND is_active=1
                UNION
                SELECT user_id, CAST(attended_date AS DATE)
                FROM classmate.fact_classmate_self_study
                WHERE user_id IN ({ph}) AND status=2 AND is_deleted=0
            ) src
            WHERE activity_date IS NOT NULL
              AND activity_date >= CAST(DATEADD(day,-89,GETDATE()) AS DATE)
              AND activity_date <= CAST(GETDATE() AS DATE)
            GROUP BY user_id""",
            tuple(cold) * 3,
        )
        uid_active_days_90 = {r["user_id"]: int(r["ad90"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("_batch_tier_map consistency failed: %s", exc)

    all_tier_scores = get_all_tier_scores()
    sorted_scores   = sorted(all_tier_scores.values(), reverse=True)
    total_pop       = len(sorted_scores)

    # Global avg 30d recency — matches the denominator in calculate_tier() and
    # refresh_tier_scores_cache(), so all views are consistent.
    _avg_cached = get_cache("company_avg_30d_credits")
    global_avg_30d = float(_avg_cached["result"]["avg"]) if _avg_cached else max(
        sum(uid_recency_30d.values()) / max(len(uid_recency_30d), 1), 1.0
    )
    if global_avg_30d == 0:
        global_avg_30d = 1.0

    for uid in cold:
        tc   = uid_alltime_credits.get(uid, 0.0)
        ad90 = uid_active_days_90.get(uid, 0)
        u30d = uid_recency_30d.get(uid, 0.0)

        credits_score     = round(min(tc / 500 * 100, 100), 1)
        consistency_score = round(ad90 / 90 * 100, 1)
        recency_score     = round(min(u30d / global_avg_30d * 50, 100), 1)

        # Skill: average over the 5 axes (matches calculate_tier's /5), else 50.0
        if uid in team_norm:
            skill_score = round(
                sum(v for k, v in team_norm[uid].items() if not k.startswith("_")) / 5,
                1,
            )
        else:
            skill_score = 50.0

        tier_score = (
            credits_score     * 0.30
            + skill_score     * 0.35
            + consistency_score * 0.20
            + recency_score   * 0.15
        )

        if total_pop > 0:
            rank       = sum(1 for s in sorted_scores if s > tier_score)
            approx_pct = rank / total_pop * 100
        else:
            approx_pct = 50.0

        emp_tier, next_tier = _percentile_to_tier(approx_pct)
        scored_by = team_norm.get(uid, {}).get("_scored_by", "keywords")

        set_cache(f"tier_{uid}", {
            "current_tier":      emp_tier,
            "next_tier":         next_tier,
            "tier_progress":     0,
            "percentile":        round(approx_pct, 1),
            "total_credits":     round(tc, 1),
            "tier_score":        round(tier_score, 1),
            "credits_score":     credits_score,
            "skill_score":       skill_score,
            "consistency_score": consistency_score,
            "recency_score":     recency_score,
            "scored_by":         scored_by,
        }, "computed", ttl_hours=25)
        tier_map[uid] = emp_tier

    return tier_map


def _build_people_list(mgr_id: int, filter_val: str) -> list:
    from services.skill_service import get_team_skill_scores
    from nova_db.gpt_cache import get_cache, set_cache

    _cache_key = f"people_list_{mgr_id}_{filter_val}"
    _cached = get_cache(_cache_key)
    if _cached:
        return _cached["result"]

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

    try:
        team_norm = get_team_skill_scores(uids)
    except Exception as exc:
        logger.warning("get_team_skill_scores failed: %s", exc)
        team_norm = {}

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
            "tier":                 emp_tier,
            "credits_this_quarter": round(emp_credits, 1),
            "streak":               0,
            "ai_proficiency":       ai_proficiency,
            "status":               emp_status,
            "last_active":          last_active_str,
            "scored_by":            scored_by,
        })

    set_cache(_cache_key, employees, "computed", ttl_hours=25)
    return employees


@router.get("/manager/people")
async def manager_people(
    filter: str = Query("all", pattern="^(all|on_track|at_risk)$"),
    user: CurrentUser = Depends(get_current_user),
):
    _require_manager(user)
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")

    mgr_id = user.classmate_user_id
    try:
        employees = await _run(_build_people_list, mgr_id, filter)
    except Exception as exc:
        logger.warning("Fabric unavailable for manager people uid=%s: %s", mgr_id, exc)
        raise HTTPException(status_code=503, detail="Data unavailable")

    return {"employees": employees}


@router.get("/manager/people/search")
async def manager_people_search(
    request: Request,
    q: str = Query("", min_length=0, max_length=100),
    user: CurrentUser = Depends(get_current_user),
):
    # When impersonating, get_current_user returns the impersonated user's identity.
    # Read X-Nova-Dev-User directly so the exec's identity is used for auth checks.
    dev_header = request.headers.get("X-Nova-Dev-User")
    signed_in_uid = int(dev_header) if dev_header and dev_header.isdigit() else user.classmate_user_id

    if signed_in_uid not in EXEC_USER_IDS:
        _require_manager(user)
    if not q or not q.strip():
        return {"employees": [], "search_scope": "none"}

    uid     = signed_in_uid
    q_lower = q.strip().lower()

    if uid is None:
        return {"employees": [], "search_scope": "dev"}

    try:
        if uid in EXEC_USER_IDS and uid in RECURSIVE_USER_IDS:
            try:
                rows  = await _run(_search_recursive, uid, q_lower)
                scope = "recursive"
            except Exception:
                rows  = await _run(_search_company_wide, q_lower)
                scope = "company"
        elif uid in EXEC_USER_IDS:
            rows  = await _run(_search_company_wide, q_lower)
            scope = "company"
        else:
            rows  = await _run(_search_direct_reports, uid, q_lower)
            scope = "direct"

        uids     = [r["user_id"] for r in rows]
        enriched = await _run(_enrich_search_results, uids, rows)

    except Exception as exc:
        logger.warning("Search failed uid=%s q=%s: %s", uid, q, exc)
        return {"employees": [], "search_scope": "error"}

    return {"employees": enriched, "search_scope": scope}
