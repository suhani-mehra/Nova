"""
routers/employee.py
Employee-facing endpoints: /api/employee/dashboard and /api/employee/team.
"""

import asyncio
import logging
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import CurrentUser, get_current_user
from core.database import query
from core.queries import get_direct_reports
from nova_db.badges import get_user_badges
from nova_db.congrats import get_congrats_received_count
from services.tier_service import calculate_tier
from services.streak_service import calculate_streak
from services.skill_service import calculate_skill_radar, get_team_skill_scores
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

_SQLITE_DB = Path(__file__).parent.parent / "nova_local.db"


async def _run(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


def _get_direct_report_ids(manager_id: int) -> list:
    rows = query(
        """
        SELECT DISTINCT user_id
        FROM   dim_classmate_employee_profile
        WHERE  manager      = ?
          AND  is_active    = 1
          AND  is_deleted   = 0
          AND  etl_isactive = 1
          AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
        """,
        (manager_id,),
    )
    return [r["user_id"] for r in rows]


def _get_team_congrats_week(manager_id: int) -> int:
    uids = _get_direct_report_ids(manager_id)
    if not uids:
        return 0
    try:
        conn = sqlite3.connect(str(_SQLITE_DB))
        conn.row_factory = sqlite3.Row
        ph = ",".join("?" * len(uids))
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM   congrats
            WHERE  created_at >= datetime('now', '-7 days')
              AND  (sender_user_id IN ({ph}) OR receiver_user_id IN ({ph}))
            """,
            uids * 2,
        ).fetchone()
        conn.close()
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


_TRAINING_KEYWORDS = [
    'iso ', 'isms', 'mandatory ', 'compliance', 'awareness on',
    'waste reduction', 'hazardous', 'workplace safety', 'energy and climate',
    'information security training', 'fire safety', 'anti-bribery', 'anti bribery',
    'code of conduct', 'data privacy', 'gdpr', 'posh', 'sexual harassment',
]


def _is_training_module_by_name(name: str) -> bool:
    nl = name.lower()
    return any(kw in nl for kw in _TRAINING_KEYWORDS)


def _filter_training_modules(courses: list) -> list:
    """Remove training/compliance modules. Uses a combination of:
    1. Explicit training keyword check (highest priority)
    2. GPT vertical scores from SQLite (0 across all = training)
    3. Keyword category classifier fallback
    """
    if not courses:
        return courses
    names = [c["course_name"] for c in courses]
    scores_by_name: dict = {}
    try:
        conn = sqlite3.connect(str(_SQLITE_DB))
        conn.row_factory = sqlite3.Row
        ph = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT item_name, ai, cloud, frontend, backend, data FROM course_vertical_scores WHERE item_name IN ({ph})",
            names,
        ).fetchall()
        conn.close()
        scores_by_name = {r["item_name"]: r for r in rows}
    except Exception:
        pass

    from services.skill_service import _classify
    result = []
    for c in courses:
        name = c["course_name"]
        # Always filter obvious compliance/training modules by name
        if _is_training_module_by_name(name):
            continue
        if name in scores_by_name:
            r = scores_by_name[name]
            total = (r["ai"] or 0) + (r["cloud"] or 0) + (r["frontend"] or 0) + (r["backend"] or 0) + (r["data"] or 0)
            # Keep if scores show real vertical content OR keyword classifier recognises it
            if total >= 5 or _classify(name) is not None:
                result.append(c)
        else:
            # Not scored yet — keep unless keyword classifier explicitly rejects it
            # (unrecognised but non-training names pass through)
            result.append(c)
    return result


def _get_team_completions_delta(manager_id: int, uid: int) -> float:
    """% change in course completions by team (peers + direct reports) this week vs last week."""
    rows = query(
        """
        SELECT
            SUM(CASE WHEN completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days') THEN 1 ELSE 0 END) AS this_week,
            SUM(CASE WHEN completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-14 days')
                      AND completed_on <  strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')  THEN 1 ELSE 0 END) AS last_week
        FROM vw_classmate_trainings
        WHERE status = 4052
          AND user_id IN (
              SELECT DISTINCT user_id
              FROM   dim_classmate_employee_profile
              WHERE  manager IN (?,?) AND is_active=1 AND is_deleted=0 AND etl_isactive=1
                AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          )
          AND completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-14 days')
        """,
        (manager_id, uid),
    )
    if not rows:
        return 0.0
    this_week = int(rows[0]["this_week"] or 0)
    last_week = int(rows[0]["last_week"] or 0)
    if last_week == 0:
        return 0.0 if this_week == 0 else 100.0
    return round((this_week - last_week) / last_week * 100, 1)


def _get_team_top_courses(manager_id: int, uid: int) -> list:
    """Top 20 most-completed courses by the full team (peers + direct reports)."""
    from services.skill_service import _classify
    rows = query(
        """
        SELECT vt.course_name, COUNT(*) AS completion_count
        FROM   vw_classmate_trainings vt
        WHERE  vt.status = 4052
          AND  vt.user_id IN (
              SELECT DISTINCT user_id
              FROM   dim_classmate_employee_profile
              WHERE  manager IN (?,?) AND is_active=1 AND is_deleted=0 AND etl_isactive=1
                AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          )
        GROUP BY vt.course_name
        ORDER BY completion_count DESC
        LIMIT 20
        """,
        (manager_id, uid),
    )
    return [
        {
            "course_name":      r["course_name"],
            "completion_count": int(r["completion_count"]),
            "category":         _classify(r["course_name"] or "") or "Other",
        }
        for r in rows
    ]


def _get_team_reco_courses(manager_id: int, uid: int) -> list:
    """Top 20 team-completed courses the given user hasn't finished, with category."""
    from services.skill_service import _classify
    rows = query(
        """
        SELECT vt.course_name, COUNT(*) AS completion_count
        FROM   vw_classmate_trainings vt
        WHERE  vt.status = 4052
          AND  vt.user_id IN (
              SELECT DISTINCT user_id
              FROM   dim_classmate_employee_profile
              WHERE  manager IN (?,?) AND is_active=1 AND is_deleted=0 AND etl_isactive=1
                AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          )
          AND  vt.course_name NOT IN (
              SELECT DISTINCT course_name
              FROM   vw_classmate_trainings
              WHERE  user_id=? AND status=4052
          )
        GROUP BY vt.course_name
        ORDER BY completion_count DESC
        LIMIT 20
        """,
        (manager_id, uid, uid),
    )
    return [
        {
            "course_name":      r["course_name"],
            "completion_count": int(r["completion_count"]),
            "category":         _classify(r["course_name"] or "") or "Other",
        }
        for r in rows
    ]


def _get_fallback_reco_courses(uid: int) -> list:
    """Random Classmate-visible courses biased to the user's own learning — used when
    the team has no (non-ISO) completed courses to recommend yet."""
    from services.skill_service import _classify
    rows = query(
        """
        SELECT DISTINCT sc.id, sc.name
        FROM dim_classmate_second_level_category sc
        JOIN dim_classmate_content_mapping cm
          ON cm.second_level_category_id = sc.id AND cm.is_deleted = 0
        WHERE sc.etl_isactive=1 AND sc.is_active=1 AND sc.is_deleted=0 AND sc.is_private=0
          AND sc.id NOT IN (
              SELECT second_level_category_id FROM vw_classmate_trainings
              WHERE user_id=? AND status=4052
          )
        """,
        (uid,),
    )
    catalogue = [{"course_name": r["name"]} for r in rows
                 if r["name"] and not _is_training_module_by_name(r["name"])]
    # Bias toward categories the user has completed / is currently studying.
    hist = query(
        "SELECT DISTINCT course_name FROM vw_classmate_trainings "
        "WHERE user_id=? AND status IN (4052,4035)",
        (uid,),
    )
    known = {_classify(h["course_name"] or "") for h in hist}
    known.discard(None)
    preferred = [c for c in catalogue if _classify(c["course_name"]) in known]
    pool = preferred or catalogue
    random.shuffle(pool)
    return [
        {
            "course_name":      c["course_name"],
            "completion_count": 0,
            "category":         _classify(c["course_name"]) or "Other",
        }
        for c in pool[:4]
    ]


def _get_inprogress(user_id: int) -> dict | None:
    rows = query(
        """
        SELECT id, course_name
        FROM   vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4035
        ORDER BY start_date DESC
        LIMIT 1
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
            FROM dim_classmate_employee_profile
            WHERE etl_isactive = 1
              AND is_active    = 1
              AND is_deleted   = 0
              AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
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
        raise HTTPException(status_code=503, detail="No user identity")

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
        logger.warning("Warehouse unavailable for dashboard uid=%s: %s", uid, exc)
        raise HTTPException(status_code=503, detail="Data unavailable")

    return {
        "tier": {
            "current":       tier["current_tier"],
            "next":          tier["next_tier"],
            "progress":      tier["tier_progress"],
            "percentile":    tier["percentile"],
            "total_credits": tier["total_credits"],
            "scored_by":     tier.get("scored_by", "keywords"),
        },
        "streak": {
            "current":       streak["current_streak"],
            "week_map":      streak["week_map"],
            "learning_time": streak["learning_time"],
        },
        "skills": {k: v for k, v in skills.items() if not k.startswith("_")},
        "continue_course": inprogress,
        "recommended": {
            **recommended,
            "scored_by": recommended.get("scored_by", "keywords"),
        },
        "badges": get_user_badges(uid),
        "congrats_received": get_congrats_received_count(uid),
    }


@router.get("/employee/team")
async def employee_team(user: CurrentUser = Depends(get_current_user)):
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")

    uid = user.classmate_user_id
    try:
        # Always show the team the employee belongs to: everyone under their own manager.
        manager_id = await _run(_get_manager_id, uid)

        if manager_id is None:
            fallback = await _run(_get_fallback_reco_courses, uid)
            return {"accomplishments": [], "popular_courses": fallback,
                    "popular_source": "fallback", "highlights": {}}

        accomplishments, top_courses, reco_courses, highlights, completions_delta, congrats_count = await asyncio.gather(
            _run(get_team_accomplishments, manager_id, 14, uid),
            _run(_get_team_top_courses, manager_id, uid),
            _run(_get_team_reco_courses, manager_id, uid),
            _run(get_team_highlights, manager_id),
            _run(_get_team_completions_delta, manager_id, uid),
            _run(_get_team_congrats_week, manager_id),
        )
    except Exception as exc:
        logger.warning("Warehouse unavailable for team uid=%s: %s", uid, exc)
        raise HTTPException(status_code=503, detail="Data unavailable")

    # Most completed non-training course for the highlights banner
    filtered_top = _filter_training_modules(top_courses)
    top_course_name = filtered_top[0]["course_name"] if filtered_top else None

    # Recommendations: team courses user hasn't done, training modules filtered out, top 4
    filtered_reco = _filter_training_modules(reco_courses)
    if not top_course_name and filtered_reco:
        top_course_name = filtered_reco[0]["course_name"]

    # If the team has nothing to recommend yet, fall back to visible courses biased
    # to the user's own learning, and tell the frontend to show the waiting message.
    team_reco = filtered_reco[:4]
    if team_reco:
        popular_courses, popular_source = team_reco, "team"
    else:
        popular_courses = await _run(_get_fallback_reco_courses, uid)
        popular_source = "fallback"

    highlights["time_delta_pct"] = completions_delta
    return {
        "accomplishments":    accomplishments,
        "popular_courses":    popular_courses,
        "popular_source":     popular_source,
        "top_course":         top_course_name,
        "highlights":         highlights,
        "congrats_this_week": congrats_count,
    }


@router.get("/employee/teammates")
async def employee_teammates(user: CurrentUser = Depends(get_current_user)):
    """Return all active teammates (direct reports of the same manager) for the compare picker."""
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")
    uid = user.classmate_user_id
    manager_id = await _run(_get_manager_id, uid)
    if manager_id is None:
        return {"teammates": []}
    reports = await _run(get_direct_reports, None, manager_id)
    # Exclude the requesting user themselves
    teammates = [{"user_id": r["user_id"], "name": r["name"]} for r in reports if r["user_id"] != uid]
    return {"teammates": teammates}


@router.get("/employee/compare/{target_user_id}")
async def employee_compare(target_user_id: int, user: CurrentUser = Depends(get_current_user)):
    """Return skill radar data for a teammate so the frontend can overlay it on the user's own radar."""
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")

    uid = user.classmate_user_id

    # Validate: both users must share the same manager (i.e. be on the same team).
    my_manager     = await _run(_get_manager_id, uid)
    their_manager  = await _run(_get_manager_id, target_user_id)
    if my_manager is None or my_manager != their_manager:
        raise HTTPException(status_code=403, detail="Not on the same team")

    # Fetch the target's name and 5-axis skill scores.
    name_rows = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: query(
            """
            WITH lp AS (
                SELECT display_name, user_id,
                       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY modified_on DESC) AS rn
                FROM dim_classmate_employee_profile
                WHERE etl_isactive = 1 AND is_active = 1 AND is_deleted = 0
                  AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
            )
            SELECT display_name FROM lp WHERE rn = 1 AND user_id = ?
            """,
            (target_user_id,),
        ),
    )
    name = name_rows[0]["display_name"] if name_rows else f"User {target_user_id}"

    scores_map = await _run(get_team_skill_scores, [target_user_id])
    user_scores = scores_map.get(target_user_id, {})

    axes   = ["AI", "Cloud", "Frontend", "Backend", "Data"]
    values = [round(user_scores.get(ax, 0.0), 1) for ax in axes]

    return {"name": name, "axes": axes, "scores": values}
