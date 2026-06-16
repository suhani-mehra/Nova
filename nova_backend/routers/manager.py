"""
routers/manager.py
Manager-only endpoints: /api/manager/overview, /api/manager/teams, /api/manager/people.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.auth import CurrentUser, get_current_user
from core.database import query
from services.tier_service import get_all_user_tiers, calculate_tier
from services.streak_service import calculate_streak
from services.skill_service import get_team_ai_proficiency, calculate_ai_proficiency
from services.team_service import get_at_risk_employees

logger = logging.getLogger(__name__)
router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=8)

# ── Placeholder data (dev mode) ───────────────────────────────────────────────

_PLACEHOLDER_OVERVIEW = {
    "kpis": {
        "total_team":           8,
        "active_this_week":     5,
        "ai_proficient_count":  3,
        "ai_proficient_pct":    37.5,
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
            "name":            "Engineering",
            "headcount":       5,
            "avg_credits":     28.4,
            "ai_proficient_pct": 40.0,
            "top_course":      "Python for Data Science",
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
    # In dev mode classmate_user_id is None; role is not yet real, so allow through
    if user.classmate_user_id is not None and user.role != "manager":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager role required",
        )


def _get_direct_reports(manager_user_id: int) -> list[dict]:
    return query(
        """
        SELECT ep.user_id, ep.display_name, ep.department_code
        FROM   classmate.dim_classmate_employee_profile ep
        WHERE  ep.manager    = ?
          AND  ep.is_active  = 1
          AND  ep.is_deleted = 0
        ORDER BY ep.display_name
        """,
        (manager_user_id,),
    )


def _get_monthly_trend(manager_user_id: int) -> list[dict]:
    rows = query(
        """
        SELECT
            FORMAT(vt.completed_on, 'MMM') AS month,
            MONTH(vt.completed_on)          AS month_num,
            YEAR(vt.completed_on)           AS year_num,
            SUM(vt.learning_credits)        AS credits,
            COUNT(*)                        AS completions
        FROM   classmate.vw_classmate_trainings vt
        JOIN   classmate.dim_classmate_employee_profile ep ON ep.user_id = vt.user_id
        WHERE  ep.manager    = ?
          AND  ep.is_deleted = 0
          AND  vt.status     = 4052
          AND  vt.completed_on >= DATEADD(month, -6, GETDATE())
        GROUP BY FORMAT(vt.completed_on, 'MMM'),
                 MONTH(vt.completed_on),
                 YEAR(vt.completed_on)
        ORDER BY year_num, month_num
        """,
        (manager_user_id,),
    )
    return [
        {
            "month":       r["month"],
            "credits":     round(float(r["credits"] or 0), 1),
            "completions": r["completions"],
        }
        for r in rows
    ]


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
            SELECT user_id, SUM(total_credits) AS credits
            FROM   classmate.mv_employee_year_quarter_credits
            WHERE  user_id IN ({placeholders})
              AND  year    = YEAR(GETDATE())
              AND  quarter = DATEPART(quarter, GETDATE())
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
    reports, monthly_trend, at_risk, ai_prof = await asyncio.gather(
        _run(_get_direct_reports, mgr_id),
        _run(_get_monthly_trend, mgr_id),
        _run(get_at_risk_employees, mgr_id),
        _run(get_team_ai_proficiency, mgr_id),
    )

    uids = [r["user_id"] for r in reports]
    active_week = await _run(_active_this_week_count, uids)
    avg_q_credits = await _run(_avg_credits_this_quarter, uids)

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
    reports = await _run(_get_direct_reports, mgr_id)
    if not reports:
        return {"departments": []}

    uids = [r["user_id"] for r in reports]
    all_tiers, ai_prof = await asyncio.gather(
        _run(get_all_user_tiers),
        _run(get_team_ai_proficiency, mgr_id),
    )

    # Group by department
    from collections import defaultdict
    dept_members: dict[str, list[int]] = defaultdict(list)
    uid_to_dept: dict[int, str] = {}
    for r in reports:
        dept = r["department_code"] or "Unknown"
        dept_members[dept].append(r["user_id"])
        uid_to_dept[r["user_id"]] = dept

    # credits per user this quarter
    if uids:
        placeholders = ",".join("?" * len(uids))
        credit_rows = query(
            f"""
            SELECT user_id, SUM(total_credits) AS credits
            FROM   classmate.mv_employee_year_quarter_credits
            WHERE  user_id IN ({placeholders})
              AND  year    = YEAR(GETDATE())
              AND  quarter = DATEPART(quarter, GETDATE())
            GROUP BY user_id
            """,
            tuple(uids),
        )
        uid_credits = {r["user_id"]: float(r["credits"] or 0) for r in credit_rows}
    else:
        uid_credits = {}

    # top course per department
    top_course_rows = query(
        f"""
        SELECT ep.department_code, vt.course_name, COUNT(*) AS cnt
        FROM   classmate.vw_classmate_trainings vt
        JOIN   classmate.dim_classmate_employee_profile ep ON ep.user_id = vt.user_id
        WHERE  vt.user_id IN ({",".join("?" * len(uids))})
          AND  vt.status   = 4052
        GROUP BY ep.department_code, vt.course_name
        ORDER BY cnt DESC
        """,
        tuple(uids),
    ) if uids else []

    dept_top_course: dict[str, str] = {}
    seen_depts = set()
    for r in top_course_rows:
        d = r["department_code"] or "Unknown"
        if d not in seen_depts:
            dept_top_course[d] = r["course_name"]
            seen_depts.add(d)

    departments = []
    tier_keys = ["platinum", "diamond", "gold", "silver", "bronze", "starter"]
    for dept, dept_uids in dept_members.items():
        dept_credits = [uid_credits.get(uid, 0.0) for uid in dept_uids]
        avg_c = round(sum(dept_credits) / len(dept_credits), 1) if dept_credits else 0.0

        tier_dist = {k: 0 for k in tier_keys}
        for uid in dept_uids:
            t = all_tiers.get(uid, "starter")
            tier_dist[t] = tier_dist.get(t, 0) + 1

        # ai proficient count for this dept
        dept_ai_count = sum(
            1 for uid in dept_uids
            if all_tiers.get(uid, "starter") in ("platinum", "diamond")  # rough proxy
        )

        departments.append({
            "name":               dept,
            "headcount":          len(dept_uids),
            "avg_credits":        avg_c,
            "ai_proficient_pct":  round(dept_ai_count / len(dept_uids) * 100, 1),
            "top_course":         dept_top_course.get(dept, "N/A"),
            "tier_distribution":  tier_dist,
        })

    return {"departments": departments}


@router.get("/manager/people")
async def manager_people(
    filter: str = Query("all", pattern="^(all|thriving|at_risk)$"),
    user: CurrentUser = Depends(get_current_user),
):
    _require_manager(user)
    if user.classmate_user_id is None:
        return _PLACEHOLDER_PEOPLE

    mgr_id = user.classmate_user_id
    reports = await _run(_get_direct_reports, mgr_id)
    if not reports:
        return {"employees": []}

    uids = [r["user_id"] for r in reports]
    uid_to_report = {r["user_id"]: r for r in reports}
    placeholders = ",".join("?" * len(uids))

    all_tiers = await _run(get_all_user_tiers)

    # credits this quarter
    credit_rows = query(
        f"""
        SELECT user_id, SUM(total_credits) AS credits
        FROM   classmate.mv_employee_year_quarter_credits
        WHERE  user_id IN ({placeholders})
          AND  year    = YEAR(GETDATE())
          AND  quarter = DATEPART(quarter, GETDATE())
        GROUP BY user_id
        """,
        tuple(uids),
    )
    uid_q_credits = {r["user_id"]: float(r["credits"] or 0) for r in credit_rows}
    avg_q_credits = (
        sum(uid_q_credits.values()) / len(uid_q_credits)
        if uid_q_credits else 0.0
    )

    # last activity date
    last_active_rows = query(
        f"""
        SELECT user_id, MAX(credit_date) AS last_date
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id IN ({placeholders})
          AND  is_deleted = 0
        GROUP BY user_id
        """,
        tuple(uids),
    )
    uid_last_active = {r["user_id"]: r["last_date"] for r in last_active_rows}

    from datetime import date, timedelta
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

        q_credits = uid_q_credits.get(uid, 0.0)
        active_7 = days_inactive <= 7

        if active_7 and q_credits >= avg_q_credits:
            emp_status = "thriving"
        elif days_inactive >= 14 or q_credits < avg_q_credits * 0.5:
            emp_status = "at_risk"
        else:
            emp_status = "on_track"

        if filter == "thriving" and emp_status != "thriving":
            continue
        if filter == "at_risk" and emp_status != "at_risk":
            continue

        # streak — lightweight: just look at last 7 days activity
        streak_rows = query(
            """
            SELECT credit_date, SUM(duration) AS dur
            FROM   classmate.fact_classmate_learning_credit
            WHERE  user_id    = ?
              AND  is_deleted = 0
              AND  credit_date >= DATEADD(day, -30, GETDATE())
            GROUP BY credit_date
            ORDER BY credit_date DESC
            """,
            (uid,),
        )
        streak_days = {
            (r2["credit_date"].date() if hasattr(r2["credit_date"], "date") else r2["credit_date"])
            for r2 in streak_rows
            if (r2["dur"] or 0) >= 1800
        }
        streak_count = 0
        check = today if today in streak_days else today - timedelta(days=1)
        while check in streak_days:
            streak_count += 1
            check -= timedelta(days=1)

        employees.append({
            "user_id":               uid,
            "name":                  r["display_name"],
            "department":            r["department_code"] or "Unknown",
            "tier":                  all_tiers.get(uid, "starter"),
            "credits_this_quarter":  round(q_credits, 1),
            "streak":                streak_count,
            "ai_proficiency":        0.0,  # expensive to compute per user; omit in list view
            "status":                emp_status,
            "last_active":           last_active_str,
        })

    return {"employees": employees}
