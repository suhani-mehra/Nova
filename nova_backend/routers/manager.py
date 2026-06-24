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


# ── Exec access sets ──────────────────────────────────────────────────────────

EXEC_USER_IDS: set[int] = {5575, 16467, 16465, 16470}  # hardcoded + DB-resolved
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
        {"month": "Q1 '25", "credits": 0.0, "retention": 0.0},
        {"month": "Q2 '25", "credits": 0.0, "retention": 0.0},
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
            "ai_trend_pct":         0.0,
            "ai_trend_dir":         "flat",
            "status":               "thriving",
            "last_active":          "2025-06-14",
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
    rows = _query("""
        SELECT COUNT(DISTINCT lc.user_id) AS cnt
        FROM classmate.fact_classmate_learning_credit lc
        JOIN (
            SELECT DISTINCT user_id
            FROM classmate.dim_classmate_employee_profile
            WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
        ) ep ON ep.user_id = lc.user_id
        WHERE lc.is_deleted=0
          AND lc.credit_date >= DATEADD(day,-7,GETDATE())
    """)
    return int(rows[0]["cnt"] or 0) if rows else 0


def _get_company_active_prev_week() -> int:
    rows = _query("""
        SELECT COUNT(DISTINCT lc.user_id) AS cnt
        FROM classmate.fact_classmate_learning_credit lc
        JOIN (
            SELECT DISTINCT user_id
            FROM classmate.dim_classmate_employee_profile
            WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
        ) ep ON ep.user_id = lc.user_id
        WHERE lc.is_deleted=0
          AND lc.credit_date >= DATEADD(day,-14,GETDATE())
          AND lc.credit_date <  DATEADD(day,-7,GETDATE())
    """)
    return int(rows[0]["cnt"] or 0) if rows else 0


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
    Bundles four company-wide metrics into one cached call (1 h TTL).
    Called from manager_overview via asyncio.gather.
    """
    from nova_db.gpt_cache import get_cache, set_cache
    CACHE_KEY = "company_overview_stats"
    cached = get_cache(CACHE_KEY)
    if cached:
        return cached["result"]
    try:
        headcount   = _get_company_headcount()
        active_week = _get_company_active_this_week()
        prev_week   = _get_company_active_prev_week()
        avg_credits = _get_company_avg_credits_this_quarter()
        result = {
            "headcount":                headcount,
            "active_this_week":         active_week,
            "active_prev_week":         prev_week,
            "avg_credits_this_quarter": avg_credits,
        }
        set_cache(CACHE_KEY, result, "computed", ttl_hours=1)
        return result
    except Exception as exc:
        logger.warning("_compute_company_overview_stats failed: %s", exc)
        return {
            "headcount": 0, "active_this_week": 0,
            "active_prev_week": 0, "avg_credits_this_quarter": 0.0,
        }


def _compute_company_retention() -> dict:
    """
    Retention = % of learners active in [-60,-30] who also appear in [-30,0].
    Trend     = current rate minus the same metric one window earlier.
    Cached 6 h.
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

        rows = _query("""
            SELECT DISTINCT user_id,
                CASE
                    WHEN credit_date >= DATEADD(day,-30,GETDATE())
                    THEN 'w0'
                    WHEN credit_date >= DATEADD(day,-60,GETDATE())
                         AND credit_date < DATEADD(day,-30,GETDATE())
                    THEN 'w1'
                    WHEN credit_date >= DATEADD(day,-90,GETDATE())
                         AND credit_date < DATEADD(day,-60,GETDATE())
                    THEN 'w2'
                END AS window
            FROM classmate.fact_classmate_learning_credit
            WHERE is_deleted=0
              AND credit_date >= DATEADD(day,-90,GETDATE())
              AND user_id IS NOT NULL
        """)
        w0: set = set()
        w1: set = set()
        w2: set = set()
        for r in rows:
            uid = r["user_id"]
            if uid not in all_uids:
                continue
            w = r["window"]
            if w == "w0":   w0.add(uid)
            elif w == "w1": w1.add(uid)
            elif w == "w2": w2.add(uid)

        current_rate = round(len(w1 & w0) / len(w1) * 100, 1) if w1 else 0.0
        prev_rate    = round(len(w2 & w1) / len(w2) * 100, 1) if w2 else 0.0
        trend_pct    = round(current_rate - prev_rate, 1)
        trend_dir    = "up" if trend_pct > 0 else "down" if trend_pct < 0 else "flat"

        result = {"rate": current_rate, "trend_pct": trend_pct, "trend_dir": trend_dir}
        set_cache(CACHE_KEY, result, "computed", ttl_hours=6)
        return result
    except Exception as exc:
        logger.warning("_compute_company_retention failed: %s", exc)
        return {"rate": 0.0, "trend_pct": 0.0, "trend_dir": "flat"}


def _compute_company_at_risk_count() -> dict:
    """
    At risk = inactive ≥14 days AND credits < 50% of company avg,
              OR inactive ≥30 days regardless.
    Returns {"count": int, "trend_pct": float, "trend_dir": str}.
    Trend compares against a weekly baseline stored in gpt_cache (7-day TTL).
    The baseline is only written when it doesn't already exist, so after one week
    it naturally resets and the next run becomes the new baseline.
    Cached 6 h.
    """
    from nova_db.gpt_cache import get_cache, set_cache
    CACHE_KEY = "company_at_risk_count"
    cached = get_cache(CACHE_KEY)
    if cached:
        result = cached["result"]
        if isinstance(result, dict):
            return result

    try:
        emp_rows = _query("""
            SELECT DISTINCT user_id FROM classmate.dim_classmate_employee_profile
            WHERE etl_isactive=1 AND is_active=1 AND is_deleted=0 AND user_id IS NOT NULL
        """)
        all_uids = {r["user_id"] for r in emp_rows if r["user_id"]}
        if not all_uids:
            return {"count": 0, "trend_pct": 0.0, "trend_dir": "flat"}

        last_rows = _query("""
            SELECT user_id, MAX(credit_date) AS last_dt
            FROM classmate.fact_classmate_learning_credit
            WHERE is_deleted=0 AND user_id IS NOT NULL
            GROUP BY user_id
        """)
        uid_last: dict = {}
        for r in last_rows:
            uid = r["user_id"]
            if uid in all_uids and r["last_dt"]:
                d = r["last_dt"]
                uid_last[uid] = d.date() if hasattr(d, "date") else d

        credit_rows = _query("""
            SELECT user_id, SUM(learning_credits) AS credits
            FROM classmate.vw_classmate_trainings
            WHERE status=4052 AND completed_on >= DATEADD(day,-90,GETDATE())
              AND user_id IS NOT NULL
            GROUP BY user_id
        """)
        uid_credits: dict = {
            r["user_id"]: float(r["credits"] or 0)
            for r in credit_rows if r["user_id"] in all_uids
        }

        all_creds = list(uid_credits.values()) or [0.0]
        avg_c = sum(all_creds) / len(all_creds)
        threshold = max(avg_c * 0.5, 2.5)

        today = date.today()
        count = 0
        for uid in all_uids:
            last = uid_last.get(uid)
            days_inactive = (today - last).days if last else 999
            q_credits = uid_credits.get(uid, 0.0)
            if (days_inactive >= 14 and q_credits < threshold) or days_inactive >= 30:
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
        set_cache(CACHE_KEY, result, "computed", ttl_hours=6)
        return result
    except Exception as exc:
        logger.warning("_compute_company_at_risk_count failed: %s", exc)
        return {"count": 0, "trend_pct": 0.0, "trend_dir": "flat"}


# ── Per-person AI trend ───────────────────────────────────────────────────────

def _get_people_ai_trend(user_ids: list) -> dict:
    """
    For each uid, compare AI-relevant learning: last 30 days vs previous 30 days.
    Returns {uid: {"pct_change": float, "dir": "up"|"down"|"flat"}}.
    """
    from nova_db.course_scores import get_scores_for_items

    if not user_ids:
        return {}

    ph = ",".join("?" * len(user_ids))
    today_d = date.today()
    cutoff_30 = today_d - timedelta(days=30)
    cutoff_60 = today_d - timedelta(days=60)

    uid_items: dict = {uid: [] for uid in user_ids}
    lookup_pairs: set = set()

    try:
        course_rows = _query(
            f"""SELECT user_id, second_level_category_id AS cat_id, completed_on
                FROM classmate.vw_classmate_trainings
                WHERE user_id IN ({ph}) AND status=4052
                  AND completed_on >= DATEADD(day,-60,GETDATE())""",
            tuple(user_ids),
        )
        for r in course_rows:
            uid = r["user_id"]
            cat_id = r["cat_id"]
            co = r["completed_on"]
            if not cat_id:
                continue
            co_date = co.date() if hasattr(co, "date") else co
            uid_items[uid].append({"type": "course", "cat_id": int(cat_id), "date": co_date})
            lookup_pairs.add(("course", int(cat_id)))
    except Exception:
        pass

    try:
        cert_rows = _query(
            f"""SELECT user_id, certificate_id, completion_date AS completed_on
                FROM classmate.fact_classmate_certification
                WHERE user_id IN ({ph}) AND status=2
                  AND is_active=1 AND is_deleted=0
                  AND completion_date >= DATEADD(day,-60,GETDATE())""",
            tuple(user_ids),
        )
        for r in cert_rows:
            uid = r["user_id"]
            cert_id = r["certificate_id"]
            co = r["completed_on"]
            if not cert_id:
                continue
            co_date = co.date() if hasattr(co, "date") else co
            uid_items[uid].append({"type": "cert", "cert_id": int(cert_id), "date": co_date})
            lookup_pairs.add(("cert", int(cert_id)))
    except Exception:
        pass

    try:
        lc_rows = _query(
            f"""SELECT user_id, topic AS name, credit_date AS completed_on
                FROM classmate.fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0
                  AND credit_date >= DATEADD(day,-60,GETDATE())
                  AND topic IS NOT NULL AND topic != ''
                  AND (self_study_id IS NOT NULL
                       OR session_id IS NOT NULL
                       OR recorded_training_id IS NOT NULL)""",
            tuple(user_ids),
        )
        for r in lc_rows:
            uid  = r["user_id"]
            name = r.get("name") or ""
            co   = r["completed_on"]
            if not name:
                continue
            co_date = co.date() if hasattr(co, "date") else co
            tid = _zlib.crc32(name.encode("utf-8", errors="replace")) & 0x7FFFFFFF
            uid_items[uid].append({"type": "lc", "lc_id": tid, "date": co_date})
            lookup_pairs.add(("lc", tid))
    except Exception:
        pass

    if not lookup_pairs:
        return {uid: {"pct_change": 0.0, "dir": "flat"} for uid in user_ids}

    score_map = get_scores_for_items(list(lookup_pairs))

    result: dict = {}
    for uid in user_ids:
        this_ai = 0.0
        last_ai = 0.0
        seen_lc: set = set()
        for item in uid_items[uid]:
            itype = item["type"]
            d     = item["date"]
            if itype == "course":
                sc = score_map.get(("course", item["cat_id"]))
            elif itype == "cert":
                sc = score_map.get(("cert", item["cert_id"]))
            elif itype == "lc":
                tid = item["lc_id"]
                if tid in seen_lc:
                    continue
                seen_lc.add(tid)
                sc = score_map.get(("lc", tid))
            else:
                sc = None
            if sc is None:
                continue
            ai_val = float(sc.get("AI", 0))
            if d >= cutoff_30:
                this_ai += ai_val
            elif d >= cutoff_60:
                last_ai += ai_val

        if last_ai == 0 and this_ai == 0:
            result[uid] = {"pct_change": 0.0, "dir": "flat"}
        elif last_ai == 0:
            result[uid] = {"pct_change": 100.0, "dir": "up"}
        else:
            pct = round((this_ai - last_ai) / last_ai * 100, 1)
            pct = max(-200.0, min(200.0, pct))
            result[uid] = {
                "pct_change": pct,
                "dir": "up" if pct > 0 else "down" if pct < 0 else "flat",
            }

    return result


# ── Background jobs ───────────────────────────────────────────────────────────

def _quarter_start(q_end: date) -> date:
    """Return the first day of the quarter that ends on q_end."""
    start_month = ((q_end.month - 1) // 3) * 3 + 1
    return date(q_end.year, start_month, 1)


def _compute_quarterly_ai_proficiency() -> list:
    """
    Computes 6 quarters of AI proficiency % AND retention % across all active employees.
    Returns [{"month": "Q3 '24", "credits": 12.5, "retention": 67.3}, ...] — cached 24 h.
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

    # Retention per quarter = % of previous quarter's active users who returned.
    # quarters[0] (warm-up) has no predecessor so it stays 0 — that value is
    # never included in the result, so it's only used as the denominator for
    # the first displayed quarter's retention computation.
    quarter_retention: dict = {}
    for i, (label, _) in enumerate(quarters):
        if i == 0:
            quarter_retention[label] = 0.0
        else:
            prev_label  = quarters[i - 1][0]
            prev_active = user_active_in_quarter[prev_label]
            curr_active = user_active_in_quarter[label]
            quarter_retention[label] = (
                round(len(prev_active & curr_active) / len(prev_active) * 100, 1)
                if prev_active else 0.0
            )

    # Build result — skip the warm-up quarter (index 0), display the last 6
    result = []
    for label, cutoff in quarters[1:]:
        proficient = sum(
            1 for uid in all_uids
            if min(100.0, math.sqrt(
                sum(v for d, v in user_ai[uid] if d <= cutoff) / MASTERY_THRESHOLD
            ) * 100) >= 60.0
        )
        pct = round(proficient / total * 100, 1)
        result.append({
            "month":     label,
            "credits":   pct,
            "retention": quarter_retention.get(label, 0.0),
        })

    # If the warm-up quarter had too few users, the first displayed quarter's
    # retention is unreliable (near-zero due to low prior-period baseline rather
    # than actual churn). Backfill it with Q2's value so the line starts flat.
    warmup_label = quarters[0][0]
    warmup_users = len(user_active_in_quarter[warmup_label])
    if len(result) >= 2 and warmup_users < total * 0.10:
        result[0]["retention"] = result[1]["retention"]

    logger.info(
        "ai_proficiency_trend: complete — %d employees, latest: %.1f%% AI-proficient, %.1f%% retention",
        total,
        result[-1]["credits"] if result else 0.0,
        result[-1]["retention"] if result else 0.0,
    )
    set_cache(CACHE_KEY, result, "computed", ttl_hours=24)
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
        quarters.append({"month": label, "credits": 0.0, "retention": 0.0})
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
    set_cache(CACHE_KEY, result, "computed", ttl_hours=24)

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
    try:
        from services.skill_service import get_team_skill_scores
        team_norm = get_team_skill_scores(uids)
        uid_ai        = {uid: round(team_norm.get(uid, {}).get("AI", 0.0), 1) for uid in uids}
        uid_scored_by = {uid: team_norm.get(uid, {}).get("_scored_by", "keywords") for uid in uids}
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
        at_risk, overview_stats, retention, at_risk_count = await asyncio.gather(
            _run(get_at_risk_employees, mgr_id),
            _run(_compute_company_overview_stats),
            _run(_compute_company_retention),
            _run(_compute_company_at_risk_count),
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
        prev_week   = overview_stats["active_prev_week"]

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

        active_trend_pct = (
            round((active_week - prev_week) / prev_week * 100, 1)
            if prev_week > 0
            else (0.0 if active_week == 0 else 100.0)
        )

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


def _build_people_list(mgr_id: int, filter_val: str) -> list:
    from services.skill_service import get_team_skill_scores, AXES
    from services.tier_service import calculate_tier

    THRIVING_MIN_CREDITS  = 5.0
    AT_RISK_INACTIVE_DAYS = 14

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
    safe_avg    = max(avg_credits, THRIVING_MIN_CREDITS)

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

    try:
        trend_data = _get_people_ai_trend(uids)
    except Exception as exc:
        logger.warning("_get_people_ai_trend failed: %s", exc)
        trend_data = {}

    today = date.today()
    employees = []
    for uid in uids:
        r = uid_to_report[uid]
        last = uid_last_active.get(uid)
        if last:
            last_date = last.date() if hasattr(last, "date") else last
            days_inactive   = (today - last_date).days
            last_active_str = str(last_date)
        else:
            days_inactive   = 999
            last_active_str = "never"

        emp_credits = uid_credits.get(uid, 0.0)
        active_7    = days_inactive <= 7

        if active_7 and emp_credits >= safe_avg:
            emp_status = "thriving"
        elif days_inactive >= AT_RISK_INACTIVE_DAYS and emp_credits < safe_avg * 0.5:
            emp_status = "at_risk"
        elif days_inactive >= 30:
            emp_status = "at_risk"
        else:
            emp_status = "on_track"

        if filter_val == "thriving" and emp_status != "thriving":
            continue
        if filter_val == "at_risk" and emp_status != "at_risk":
            continue

        ai_proficiency = round(team_norm.get(uid, {}).get("AI", 0.0), 1)
        scored_by      = team_norm.get(uid, {}).get("_scored_by", "keywords")

        try:
            tier_data = calculate_tier(uid)
            emp_tier  = tier_data["current_tier"]
        except Exception:
            all_creds = list(uid_credits.values())
            max_cred  = max(all_creds) if all_creds else 0.0
            def _team_tier(credits: float) -> str:
                if max_cred == 0: return "starter"
                pct = credits / max_cred * 100
                if pct >= 90: return "platinum"
                if pct >= 70: return "diamond"
                if pct >= 50: return "gold"
                if pct >= 30: return "silver"
                if pct >= 10: return "bronze"
                return "starter"
            emp_tier = _team_tier(emp_credits)

        ai_tr = trend_data.get(uid, {})
        employees.append({
            "user_id":              uid,
            "name":                 r["name"],
            "department":           r["department"] or "Unknown",
            "tier":                 emp_tier,
            "credits_this_quarter": round(emp_credits, 1),
            "streak":               0,
            "ai_proficiency":       ai_proficiency,
            "ai_trend_pct":         ai_tr.get("pct_change", 0.0),
            "ai_trend_dir":         ai_tr.get("dir", "flat"),
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
