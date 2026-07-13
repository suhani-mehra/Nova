"""
services/recommendation_service.py
GPT-4o-mini course recommendation with SQLite cache.
"""

import json
import logging
from typing import Optional

from openai import AzureOpenAI

from core.config import settings
from core.database import query
from services.skill_service import _classify, AXES, calculate_skill_radar
from nova_db.gpt_cache import get_cache, set_cache

logger = logging.getLogger(__name__)

_in_flight: set[int] = set()

_client: Optional[AzureOpenAI] = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            api_key=settings.openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.openai_api_version,
        )
    return _client


def _get_completed_course_names(user_id: int) -> list:
    """Recent 10 completed course names, for the GPT prompt context."""
    rows = query(
        """
        SELECT course_name
        FROM   vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        ORDER BY completed_on DESC
        LIMIT 10
        """,
        (user_id,),
    )
    return [r["course_name"] for r in rows]


def _get_in_progress_course(user_id: int) -> Optional[str]:
    rows = query(
        """
        SELECT course_name
        FROM   vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4035
        ORDER BY start_date DESC
        LIMIT 1
        """,
        (user_id,),
    )
    return rows[0]["course_name"] if rows else None


def _detect_weak_skill_categories(user_id: int) -> list:
    """Two weakest skill axes from the cached radar; falls back to a fixed
    pair on any failure (radar computation, cache miss, etc.)."""
    try:
        radar = calculate_skill_radar(user_id)
        paired = sorted(zip(radar["this_month"], AXES))
        return [ax for _, ax in paired[:2]]
    except Exception as exc:
        logger.warning("get_recommendation: skill radar failed for user_id=%s: %s", user_id, exc)
        return ["AI", "Cloud"]


def _get_team_popular_courses(user_id: int) -> list:
    """Top 5 courses completed by the user's teammates (same manager)."""
    manager_rows = query(
        """
        SELECT manager FROM dim_classmate_employee_profile
        WHERE user_id = ? AND is_deleted = 0
        """,
        (user_id,),
    )
    manager_id = manager_rows[0]["manager"] if manager_rows else None
    if not manager_id:
        return []

    pop_rows = query(
        """
        SELECT vt.course_name, COUNT(*) AS cnt
        FROM   vw_classmate_trainings vt
        JOIN   dim_classmate_employee_profile ep ON ep.user_id = vt.user_id
        WHERE  ep.manager      = ?
          AND  ep.is_active    = 1
          AND  ep.is_deleted   = 0
          AND  ep.etl_isactive = 1
          AND  (ep.employee_id IS NULL OR UPPER(TRIM(ep.employee_id)) NOT LIKE 'TMP%')
          AND  ep.country_code IS NOT NULL AND UPPER(TRIM(ep.country_code)) != 'OT'
          AND  vt.status       = 4052
        GROUP BY vt.course_name
        ORDER BY cnt DESC
        LIMIT 5
        """,
        (manager_id,),
    )
    return [r["course_name"] for r in pop_rows]


def _get_available_catalogue(user_id: int) -> list:
    """Browsable courses the user hasn't completed yet."""
    rows = query(
        """
        SELECT DISTINCT sc.id, sc.name
        FROM dim_classmate_second_level_category sc
        JOIN dim_classmate_content_mapping cm
          ON cm.second_level_category_id = sc.id
         AND cm.is_deleted = 0
        WHERE sc.etl_isactive = 1
          AND sc.is_active = 1
          AND sc.is_deleted = 0
          AND sc.is_private = 0
          AND sc.id NOT IN (
              SELECT second_level_category_id
              FROM vw_classmate_trainings
              WHERE user_id=? AND status=4052
          )
        ORDER BY sc.name
        """,
        (user_id,),
    )
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def _build_candidate_list(catalogue: list, weak_cats: list) -> list:
    """Top-20 GPT-prompt candidates, weak-skill categories first."""
    weak_set = set(weak_cats)
    preferred = [c for c in catalogue if _classify(c["name"]) in weak_set]
    others = [c for c in catalogue if c not in preferred]
    return (preferred + others)[:20]


def _build_gpt_prompt(weak_cats, completed_names, popular_courses, in_progress, send_list) -> str:
    return f"""You are a learning advisor for a tech professional.
Recommend ONE course from the list below.
Skill gaps: {weak_cats}
Recently completed: {completed_names}
Team popular: {popular_courses}
Currently studying: {in_progress or 'nothing'}
Available courses (id, name):
{json.dumps(send_list, indent=2)}
Return ONLY valid JSON, no markdown:
{{"course_id":<int>,"course_name":"<str>","reason":"<max 10 words>"}}"""


def _call_gpt_for_recommendation(prompt: str) -> dict:
    """Calls the GPT client and validates the response shape. Raises on any
    failure (bad JSON, missing keys, wrong types) so the caller's existing
    except -> fallback path handles it."""
    client = _get_client()
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=120,
        timeout=10,
    )
    raw = response.choices[0].message.content.strip()
    result = json.loads(raw)
    if not all(k in result for k in ("course_id", "course_name", "reason")):
        raise ValueError("Missing keys in GPT response")
    if not isinstance(result["course_id"], int):
        raise ValueError("course_id must be int")
    return result


def get_recommendation(user_id: int, conn=None) -> dict:
    cached = get_cache(f"recommend_{user_id}")
    if cached:
        return cached["result"]

    if user_id in _in_flight:
        logger.info("Recommendation in-flight for user %s — using fallback", user_id)
        return _fallback([], [])

    completed_names = _get_completed_course_names(user_id)
    in_progress = _get_in_progress_course(user_id)
    weak_cats = _detect_weak_skill_categories(user_id)
    popular_courses = _get_team_popular_courses(user_id)
    catalogue = _get_available_catalogue(user_id)

    if not catalogue:
        return {"course_id": None, "course_name": "No courses available",
                "reason": "Catalogue is empty", "scored_by": "keywords"}

    send_list = _build_candidate_list(catalogue, weak_cats)
    prompt = _build_gpt_prompt(weak_cats, completed_names, popular_courses, in_progress, send_list)

    scored_by = "keywords"
    _in_flight.add(user_id)
    try:
        result = _call_gpt_for_recommendation(prompt)
        scored_by = "gpt"
    except Exception as exc:
        logger.warning("OpenAI recommendation failed: %s — using fallback", exc)
        result = _fallback(completed_names, catalogue)
    finally:
        _in_flight.discard(user_id)

    result["scored_by"] = scored_by
    set_cache(
        f"recommend_{user_id}",
        result,
        scored_by,
        ttl_hours=settings.openai_recommendation_cache_hours,
    )
    return result


def _fallback(completed_names: list[str], catalogue: list[dict]) -> dict:
    completed_set = {n.lower() for n in completed_names}
    for course in catalogue:
        if course["name"].lower() not in completed_set:
            return {
                "course_id":   course["id"],
                "course_name": course["name"],
                "reason":      "Popular course you haven't tried yet",
                "scored_by":   "keywords",
            }
    if catalogue:
        first = catalogue[0]
        return {"course_id": first["id"], "course_name": first["name"],
                "reason": "Recommended for you", "scored_by": "keywords"}
    return {"course_id": None, "course_name": "No courses available",
            "reason": "Catalogue is empty", "scored_by": "keywords"}
