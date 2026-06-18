"""
routers/employee.py
Employee-facing endpoints: /api/employee/dashboard and /api/employee/team.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends

from core.auth import CurrentUser, get_current_user
from core.database import query
from services.tier_service import calculate_tier
from services.streak_service import calculate_streak
from services.skill_service import calculate_skill_radar
from services.recommendation_service import get_recommendation
from services.team_service import (
    get_team_highlights,
    get_team_accomplishments,
    get_team_course_popularity,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=8)

# ── Placeholder data (dev mode, classmate_user_id is None) ───────────────────

_PLACEHOLDER_DASHBOARD = {
    "tier": {
        "current":      "gold",
        "next":         "diamond",
        "progress":     65,
        "percentile":   18.4,
        "total_credits": 142.5,
    },
    "streak": {
        "current":       7,
        "week_map":      [True, True, False, True, True, True, False],
        "learning_time": "3h 20m",
    },
    "skills": {
        "axes":       ["AI", "Cloud", "Frontend", "Backend", "Data"],
        "this_month": [72, 45, 30, 60, 50],
        "last_month": [60, 40, 35, 55, 48],
        "delta":      4,
    },
    "continue_course": {
        "name":      "Introduction to Azure OpenAI",
        "progress":  42,
        "course_id": 101,
    },
    "recommended": {
        "course_name": "MLOps: Model Deployment at Scale",
        "reason":      "Builds on your recent AI coursework",
        "course_id":   202,
    },
    "badges": [],
}

_PLACEHOLDER_TEAM = {
    "accomplishments": [
        {
            "employee_name":    "Alice Kumar",
            "course_name":      "Azure Fundamentals",
            "completed_on":     "2025-06-10",
            "learning_credits": 8.0,
            "category":         "Cloud",
        }
    ],
    "popular_courses": [
        {"course_name": "Python for Data Science", "completion_count": 5, "category": "Data"}
    ],
    "highlights": {
        "top_learner":   {"name": "Alice Kumar",  "credits": 24.0},
        "most_improved": {"name": "Bob Singh",    "delta": 12.0},
        "streak_leader": {"name": "Carol Thomas", "streak": 14},
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


def _get_inprogress(user_id: int) -> dict | None:
    rows = query(
        """
        SELECT TOP 1 id, course_name
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4035
        ORDER BY start_date DESC
        """,
        (user_id,),
    )
    if not rows:
        return None
    r = rows[0]
    return {"name": r["course_name"], "progress": 0, "course_id": r["id"]}


def _get_manager_id(user_id: int) -> int | None:
    rows = query(
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
        )
        SELECT manager
        FROM   latest_profiles
        WHERE  rn      = 1
          AND  user_id = ?
        """,
        (user_id,),
    )
    return rows[0]["manager"] if rows else None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/employee/dashboard")
async def employee_dashboard(user: CurrentUser = Depends(get_current_user)):
    if user.classmate_user_id is None:
        return _PLACEHOLDER_DASHBOARD

    uid = user.classmate_user_id
    try:
        tier, streak, skills, inprogress, recommended = await asyncio.gather(
            _run(calculate_tier, uid),
            _run(calculate_streak, uid),
            _run(calculate_skill_radar, uid),
            _run(_get_inprogress, uid),
            _run(get_recommendation, uid),
        )
    except Exception as exc:
        logger.warning("Fabric unavailable for dashboard uid=%s: %s", uid, exc)
        return _PLACEHOLDER_DASHBOARD

    return {
        "tier": {
            "current":       tier["current_tier"],
            "next":          tier["next_tier"],
            "progress":      tier["tier_progress"],
            "percentile":    tier["percentile"],
            "total_credits": tier["total_credits"],
        },
        "streak": {
            "current":       streak["current_streak"],
            "week_map":      streak["week_map"],
            "learning_time": streak["learning_time"],
        },
        "skills": skills,
        "continue_course": inprogress,
        "recommended":     recommended,
        "badges":          [],
    }


@router.get("/employee/team")
async def employee_team(user: CurrentUser = Depends(get_current_user)):
    if user.classmate_user_id is None:
        return _PLACEHOLDER_TEAM

    uid = user.classmate_user_id
    try:
        # Always show the team the employee belongs to: everyone under their own manager.
        manager_id = await _run(_get_manager_id, uid)

        if manager_id is None:
            return {"accomplishments": [], "popular_courses": [], "highlights": {}}

        accomplishments, popular_courses, highlights = await asyncio.gather(
            _run(get_team_accomplishments, manager_id, 14, uid),
            _run(get_team_course_popularity, manager_id),
            _run(get_team_highlights, manager_id),
        )
    except Exception as exc:
        logger.warning("Fabric unavailable for team uid=%s: %s", uid, exc)
        return _PLACEHOLDER_TEAM

    return {
        "accomplishments": accomplishments,
        "popular_courses": popular_courses,
        "highlights":      highlights,
    }
