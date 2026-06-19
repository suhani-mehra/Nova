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


def get_recommendation(user_id: int, conn=None) -> dict:
    cached = get_cache(f"recommend_{user_id}")
    if cached:
        return cached["result"]

    if user_id in _in_flight:
        logger.info("Recommendation in-flight for user %s — using fallback", user_id)
        return _fallback([], [])

    # Completed courses (recent 10)
    completed_rows = query(
        """
        SELECT TOP 10 course_name
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        ORDER BY completed_on DESC
        """,
        (user_id,),
    )
    completed_names = [r["course_name"] for r in completed_rows]

    inprogress_rows = query(
        """
        SELECT TOP 1 course_name
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4035
        ORDER BY start_date DESC
        """,
        (user_id,),
    )
    in_progress = inprogress_rows[0]["course_name"] if inprogress_rows else None

    # Weak skill categories from cached radar
    try:
        radar = calculate_skill_radar(user_id)
        paired = sorted(zip(radar["this_month"], AXES))
        weak_cats = [ax for _, ax in paired[:2]]
    except Exception:
        weak_cats = ["AI", "Cloud"]

    # Manager for popular courses context
    manager_rows = query(
        """
        SELECT manager FROM classmate.dim_classmate_employee_profile
        WHERE user_id = ? AND is_deleted = 0
        """,
        (user_id,),
    )
    manager_id = manager_rows[0]["manager"] if manager_rows else None

    popular_courses: list[str] = []
    if manager_id:
        pop_rows = query(
            """
            SELECT TOP 5 vt.course_name, COUNT(*) AS cnt
            FROM   classmate.vw_classmate_trainings vt
            JOIN   classmate.dim_classmate_employee_profile ep ON ep.user_id = vt.user_id
            WHERE  ep.manager    = ?
              AND  ep.is_deleted = 0
              AND  vt.status     = 4052
            GROUP BY vt.course_name
            ORDER BY cnt DESC
            """,
            (manager_id,),
        )
        popular_courses = [r["course_name"] for r in pop_rows]

    # Catalogue excluding already completed
    catalogue_rows = query(
        """
        SELECT id, name
        FROM classmate.dim_classmate_second_level_category
        WHERE is_active=1 AND is_private=0
          AND id NOT IN (
              SELECT second_level_category_id
              FROM classmate.vw_classmate_trainings
              WHERE user_id=? AND status=4052
          )
        ORDER BY name
        """,
        (user_id,),
    )
    catalogue = [{"id": r["id"], "name": r["name"]} for r in catalogue_rows]

    if not catalogue:
        return {"course_id": None, "course_name": "No courses available",
                "reason": "Catalogue is empty", "scored_by": "keywords"}

    weak_set = set(weak_cats)
    preferred = [c for c in catalogue if _classify(c["name"]) in weak_set]
    others = [c for c in catalogue if c not in preferred]
    send_list = (preferred + others)[:20]

    prompt = f"""You are a learning advisor for a tech professional.
Recommend ONE course from the list below.
Skill gaps: {weak_cats}
Recently completed: {completed_names}
Team popular: {popular_courses}
Currently studying: {in_progress or 'nothing'}
Available courses (id, name):
{json.dumps(send_list, indent=2)}
Return ONLY valid JSON, no markdown:
{{"course_id":<int>,"course_name":"<str>","reason":"<max 10 words>"}}"""

    scored_by = "keywords"
    _in_flight.add(user_id)
    try:
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
