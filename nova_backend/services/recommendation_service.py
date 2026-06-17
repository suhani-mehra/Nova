"""
services/recommendation_service.py
GPT-4o-mini course recommendation with in-memory cache.
"""

import json
import logging
import time
from typing import Optional

from openai import AzureOpenAI

from core.config import settings
from core.database import query
from services.skill_service import _classify, AXES

logger = logging.getLogger(__name__)

# in-memory cache: {user_id: {"result": dict, "ts": float}}
_cache: dict[int, dict] = {}
# tracks user_ids currently awaiting a GPT response; prevents duplicate calls
# when two requests arrive before the first one writes to _cache
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


def _cache_valid(user_id: int) -> bool:
    entry = _cache.get(user_id)
    if not entry:
        return False
    ttl = settings.openai_recommendation_cache_hours * 3600
    return (time.time() - entry["ts"]) < ttl


def get_recommendation(user_id: int, conn=None) -> dict:
    if _cache_valid(user_id):
        return _cache[user_id]["result"]

    # A concurrent request for the same user is already in flight — return the
    # cached fallback rather than fire a second identical GPT call.
    if user_id in _in_flight:
        logger.info("Recommendation in-flight for user %s — using fallback", user_id)
        return _cache[user_id]["result"] if user_id in _cache else _fallback([], [])

    # Gather context
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

    # weakest skill categories
    all_completed = query(
        """
        SELECT course_name, learning_credits
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        """,
        (user_id,),
    )
    cat_credits = {cat: 0.0 for cat in AXES}
    for r in all_completed:
        cat = _classify(r.get("course_name") or "")
        if cat:
            cat_credits[cat] += float(r.get("learning_credits") or 0)
    sorted_cats = sorted(cat_credits.items(), key=lambda x: x[1])
    weak_cats = [c[0] for c in sorted_cats[:2]]

    # manager for team context
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

    catalogue_rows = query(
        """
        SELECT id, name
        FROM   classmate.dim_classmate_second_level_category
        WHERE  is_active  = 1
          AND  is_private = 0
        ORDER BY name
        """,
    )
    catalogue = [{"id": r["id"], "name": r["name"]} for r in catalogue_rows]

    if not catalogue:
        return {"course_id": None, "course_name": "No courses available", "reason": "Catalogue is empty"}

    prompt = f"""You are a learning advisor for a tech employee at Orion Innovation.

Completed courses (recent 10): {completed_names}
Current in-progress: {in_progress}
Weakest skill areas: {weak_cats}
Popular courses on the team: {popular_courses}

Available course catalogue (id, name):
{json.dumps(catalogue[:80], indent=2)}

Pick ONE course from the catalogue above that would best help this employee grow.
Return ONLY valid JSON, no markdown, no explanation:
{{"course_id": <int>, "course_name": "<str>", "reason": "<max 12 words>"}}"""

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
    except Exception as exc:
        logger.warning("OpenAI recommendation failed: %s — using fallback", exc)
        result = _fallback(completed_names, catalogue)
    finally:
        _in_flight.discard(user_id)

    _cache[user_id] = {"result": result, "ts": time.time()}
    return result


def _fallback(completed_names: list[str], catalogue: list[dict]) -> dict:
    """Returns most popular uncompleted course from catalogue."""
    completed_set = {n.lower() for n in completed_names}
    for course in catalogue:
        if course["name"].lower() not in completed_set:
            return {
                "course_id":   course["id"],
                "course_name": course["name"],
                "reason":      "Popular course you haven't tried yet",
            }
    first = catalogue[0]
    return {"course_id": first["id"], "course_name": first["name"], "reason": "Recommended for you"}
