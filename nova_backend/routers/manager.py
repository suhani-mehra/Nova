"""
routers/manager.py
Manager-only endpoints: /api/manager/overview, /api/manager/teams, /api/manager/people.
"""

import asyncio
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.auth import CurrentUser, get_current_user
from core.database import query
from core.queries import get_direct_reports, get_manager_monthly_trend
from services.skill_service import get_team_ai_proficiency
from services.team_service import get_at_risk_employees

logger = logging.getLogger(__name__)
router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=8)

# ── Placeholder data (shown when Fabric is unreachable) ───────────────────────

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
            "streak":               7,
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
    rows = query(
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


def _avg_credits_this_quarter(uids: list[int]) -> float:
    if not uids:
        return 0.0
    placeholders = ",".join("?" * len(uids))
    rows = query(
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


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/manager/overview")
async def manager_overview(user: CurrentUser = Depends(get_current_user)):
    _require_manager(user)
    if user.classmate_user_id is None:
        return _PLACEHOLDER_OVERVIEW

    mgr_id = user.classmate_user_id
    try:
        reports, monthly_trend, at_risk, ai_prof = await asyncio.gather(
            _run(get_direct_reports, None, mgr_id),
            _run(get_manager_monthly_trend, None, mgr_id),
            _run(get_at_risk_employees, mgr_id),
            _run(get_team_ai_proficiency, mgr_id),
        )
        uids = [r["user_id"] for r in reports]
        active_week = await _run(_active_this_week_count, uids)
        avg_q_credits = await _run(_avg_credits_this_quarter, uids)
    except Exception as exc:
        logger.warning("Fabric unavailable for manager overview uid=%s: %s", mgr_id, exc)
        return _PLACEHOLDER_OVERVIEW

    return {
        "kpis": {
            "total_team":               len(uids),
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

        # Batch: credits per user (from trainings) + top course per department
        credit_rows, top_course_rows = await asyncio.gather(
            _run(query,
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
            _run(query,
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
    """Fetch and assemble people rows in one worker thread (no event-loop blocking)."""
    from datetime import date

    reports = get_direct_reports(None, mgr_id)
    if not reports:
        return []

    uids = [r["user_id"] for r in reports]
    uid_to_report = {r["user_id"]: r for r in reports}
    placeholders = ",".join("?" * len(uids))

    # Total completed credits per user (vw_classmate_trainings is confirmed to work)
    credit_rows = query(
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

    # Last activity date — optional; skip gracefully if table unavailable
    uid_last_active: dict = {}
    try:
        last_rows = query(
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

    # Relative tier based on credits within this team
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

        employees.append({
            "user_id":              uid,
            "name":                 r["name"],
            "department":           r["department"] or "Unknown",
            "tier":                 _team_tier(emp_credits),
            "credits_this_quarter": round(emp_credits, 1),
            "streak":               0,
            "ai_proficiency":       0.0,
            "status":               emp_status,
            "last_active":          last_active_str,
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
