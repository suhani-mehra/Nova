"""
services/skill_service.py
Skill radar (5 categories) and AI proficiency scoring.
"""

import logging
import zlib
from datetime import date
import re

from core.database import query
from core.config import settings

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "AI": [
        "ai", "artificial intelligence", "machine learning", "ml", "genai",
        "gpt", "llm", "prompt", "openai", "anthropic", "neural", "nlp",
        "generative", "agentic", "deep learning", "chatgpt", "copilot",
        "ai-augment",
    ],
    "Cloud": [
        "azure", "aws", "gcp", "cloud", "kubernetes", "docker", "terraform",
        "devops", "serverless", "snowflake", "infrastructure", "iaas", "paas",
        "saas", "lakehouse",
    ],
    "Frontend": [
        "react", "angular", "vue", "javascript", "typescript", "css", "html",
        "frontend", "ui", "ux", "power apps", "figma", "web", "accessibility",
        "responsive", "ux design",
    ],
    "Backend": [
        "python", "java", "node", "fastapi", "spring", "api", "rest",
        "backend", "database", "sql", "mongodb", "postgresql", ".net", "c#",
        "microservice", "graphql", "dotnet",
    ],
    "Data": [
        "data", "analytics", "power bi", "tableau", "pandas", "spark",
        "data science", "statistics", "bi", "reporting", "etl", "warehouse",
        "databricks", "dax", "data engineering",
    ],
}

AXES = ["AI", "Cloud", "Frontend", "Backend", "Data"]

# Power-curve normalization: score = (raw / MASTERY_THRESHOLD) ^ MASTERY_POWER * 100
# Power < 0.5 gives fast early progress; high threshold makes 100% very hard to reach.
MASTERY_THRESHOLD = 5000.0
MASTERY_POWER     = 0.4


def _lc_topic_id(topic: str) -> int:
    """Stable integer key for an LC topic (matches course_scoring_service.lc_topic_id)."""
    return zlib.crc32(topic.encode("utf-8", errors="replace")) & 0x7FFFFFFF


def _classify(course_name: str) -> str | None:
    """Keyword-based single-category classifier. Kept for recommendation_service.py catalogue filtering."""
    name_lower = course_name.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if len(kw) <= 3:
                if re.search(r'\b' + re.escape(kw) + r'\b', name_lower):
                    return cat
            else:
                if kw in name_lower:
                    return cat
    return None


def _keyword_scores(course_name: str) -> dict[str, int]:
    """Return a 5-vertical score dict via keyword fallback: 70 for matched category, 0 for others."""
    matched = _classify(course_name)
    return {ax: (70 if ax == matched else 0) for ax in AXES}


def calculate_skill_radar(user_id: int, conn=None) -> dict:
    from nova_db.gpt_cache import get_cache, set_cache
    from nova_db.course_scores import get_scores_for_items

    cached = get_cache(f"classify_{user_id}")
    if cached:
        logger.info("Cache hit for classify_%s", user_id)
        return cached["result"]

    completed = query(
        """
        SELECT second_level_category_id, course_name, completed_on
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        ORDER BY completed_on DESC
        """,
        (user_id,),
    )
    certs = query(
        """
        SELECT fc.certificate_id,
               dc.certificate_name AS course_name,
               fc.completion_date  AS completed_on
        FROM   classmate.fact_classmate_certification fc
        JOIN   classmate.dim_classmate_certificate dc ON dc.id = fc.certificate_id
        WHERE  fc.user_id   = ?
          AND  fc.status    = 2
          AND  fc.is_active = 1
          AND  fc.is_deleted = 0
        ORDER BY fc.completion_date DESC
        """,
        (user_id,),
    )
    lc_items = query(
        """
        SELECT CASE WHEN self_study_id IS NOT NULL       THEN 'self_study'
                    WHEN session_id IS NOT NULL           THEN 'session'
                    WHEN recorded_training_id IS NOT NULL THEN 'recorded'
               END AS item_type,
               COALESCE(self_study_id, session_id, recorded_training_id) AS item_id,
               topic AS course_name,
               credit_date AS completed_on
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id    = ?
          AND  is_deleted = 0
          AND  COALESCE(self_study_id, session_id, recorded_training_id) IS NOT NULL
        ORDER BY credit_date DESC
        """,
        (user_id,),
    )

    empty_result = {
        "axes":        AXES,
        "this_month":  [0] * 5,
        "last_month":  [0] * 5,
        "delta":       0,
        "scored_by":   "gpt",
        "_raw_scores": {ax: 0.0 for ax in AXES},
    }
    if not completed and not certs and not lc_items:
        set_cache(f"classify_{user_id}", empty_result, "gpt", ttl_hours=25)
        return empty_result

    # Build lookup pairs and item list
    lookup_pairs: list[tuple[str, int]] = []
    items: list[dict] = []
    for r in completed:
        cat_id = r["second_level_category_id"]
        name   = r["course_name"] or ""
        co     = r["completed_on"]
        items.append({"type": "course", "cat_id": cat_id, "name": name, "completed_on": co})
        if cat_id:
            lookup_pairs.append(("course", int(cat_id)))
    for r in certs:
        cert_id = r["certificate_id"]
        name    = r["course_name"] or ""
        co      = r["completed_on"]
        items.append({"type": "cert", "cert_id": cert_id, "name": name, "completed_on": co})
        if cert_id:
            lookup_pairs.append(("cert", int(cert_id)))
    seen_lc_topics: set[int] = set()
    for r in lc_items:
        name = r["course_name"] or ""
        co   = r["completed_on"]
        if not name:
            continue
        tid = _lc_topic_id(name)
        if tid in seen_lc_topics:
            continue  # deduplicate by topic per employee
        seen_lc_topics.add(tid)
        items.append({"type": "lc", "lc_id": tid, "name": name, "completed_on": co})
        lookup_pairs.append(("lc", tid))

    score_map = get_scores_for_items(list(set(lookup_pairs)))

    today          = date.today()
    start_of_month = today.replace(day=1)

    all_raw  = {ax: 0.0 for ax in AXES}   # cumulative up to today
    prev_raw = {ax: 0.0 for ax in AXES}   # cumulative up to end of last month

    for item in items:
        co = item["completed_on"]
        if co is None:
            continue
        co_date = co.date() if hasattr(co, "date") else co

        # Look up pre-scored vertical scores from SQLite
        if item["type"] == "course" and item.get("cat_id"):
            sc = score_map.get(("course", int(item["cat_id"])))
        elif item["type"] == "cert" and item.get("cert_id"):
            sc = score_map.get(("cert", int(item["cert_id"])))
        elif item["type"] == "lc" and item.get("lc_id"):
            sc = score_map.get(("lc", int(item["lc_id"])))
        else:
            sc = None

        if sc is None:
            sc = _keyword_scores(item["name"])

        for ax in AXES:
            v = float(sc.get(ax, 0))
            all_raw[ax] += v
            if co_date < start_of_month:
                prev_raw[ax] += v

    this_norm = {ax: round(min(100.0, (all_raw[ax] / MASTERY_THRESHOLD) ** MASTERY_POWER * 100), 1) for ax in AXES}
    last_norm = {ax: round(min(100.0, (prev_raw[ax] / MASTERY_THRESHOLD) ** MASTERY_POWER * 100), 1) for ax in AXES}

    delta = round(sum(this_norm[AXES[i]] - last_norm[AXES[i]] for i in range(5)) / 5)

    result = {
        "axes":        AXES,
        "this_month":  [this_norm[ax] for ax in AXES],
        "last_month":  [last_norm[ax] for ax in AXES],
        "delta":       delta,
        "scored_by":   "gpt",
        "_raw_scores": all_raw,
    }
    set_cache(f"classify_{user_id}", result, "gpt", ttl_hours=25)
    return result


def get_team_skill_scores(user_ids: list[int]) -> dict:
    from nova_db.gpt_cache import get_cache, set_cache
    from nova_db.course_scores import get_scores_for_items

    if not user_ids:
        return {}

    cached_results: dict[int, dict] = {}
    uncached_uids: list[int] = []
    for uid in user_ids:
        c = get_cache(f"classify_{uid}")
        if c:
            cached_results[uid] = c["result"]
        else:
            uncached_uids.append(uid)

    raw_scores: dict[int, dict[str, float]] = {}

    if uncached_uids:
        # If any of the completion queries fails (transient Fabric error), we must NOT
        # cache the resulting all-zero scores — doing so poisons the classify_{uid}
        # cache for 24h. Track success and only write cache entries when every query ran.
        queries_ok = True
        ph = ",".join("?" * len(uncached_uids))
        try:
            course_rows = query(
                f"""
                SELECT user_id, second_level_category_id, course_name, completed_on
                FROM classmate.vw_classmate_trainings
                WHERE user_id IN ({ph}) AND status = 4052
                """,
                tuple(uncached_uids),
            )
        except Exception as exc:
            logger.warning("get_team_skill_scores courses failed: %s", exc)
            course_rows = []
            queries_ok = False
        try:
            cert_rows = query(
                f"""
                SELECT fc.user_id,
                       fc.certificate_id,
                       dc.certificate_name AS course_name,
                       fc.completion_date  AS completed_on
                FROM classmate.fact_classmate_certification fc
                JOIN classmate.dim_classmate_certificate dc ON dc.id = fc.certificate_id
                WHERE fc.user_id IN ({ph}) AND fc.status = 2
                  AND fc.is_active = 1 AND fc.is_deleted = 0
                """,
                tuple(uncached_uids),
            )
        except Exception as exc:
            logger.warning("get_team_skill_scores certs failed: %s", exc)
            cert_rows = []
            queries_ok = False
        try:
            lc_rows = query(
                f"""
                SELECT user_id,
                       CASE WHEN self_study_id IS NOT NULL       THEN 'self_study'
                            WHEN session_id IS NOT NULL           THEN 'session'
                            WHEN recorded_training_id IS NOT NULL THEN 'recorded'
                       END AS item_type,
                       COALESCE(self_study_id, session_id, recorded_training_id) AS item_id,
                       topic AS course_name,
                       credit_date AS completed_on
                FROM classmate.fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0
                  AND COALESCE(self_study_id, session_id, recorded_training_id) IS NOT NULL
                """,
                tuple(uncached_uids),
            )
        except Exception as exc:
            logger.warning("get_team_skill_scores LC failed: %s", exc)
            lc_rows = []
            queries_ok = False

        # Collect all unique (type, id) pairs across all users for a single batch SQLite lookup
        lookup_pairs: set[tuple[str, int]] = set()
        uid_items: dict[int, list[dict]] = {uid: [] for uid in uncached_uids}

        for r in course_rows:
            uid = r["user_id"]
            cat_id = r["second_level_category_id"]
            name   = r["course_name"] or ""
            co     = r["completed_on"]
            uid_items[uid].append({"type": "course", "cat_id": cat_id, "name": name, "completed_on": co})
            if cat_id:
                lookup_pairs.add(("course", int(cat_id)))
        for r in cert_rows:
            uid = r["user_id"]
            cert_id = r["certificate_id"]
            name    = r["course_name"] or ""
            co      = r["completed_on"]
            uid_items[uid].append({"type": "cert", "cert_id": cert_id, "name": name, "completed_on": co})
            if cert_id:
                lookup_pairs.add(("cert", int(cert_id)))
        for r in lc_rows:
            uid  = r["user_id"]
            name = r["course_name"] or ""
            co   = r["completed_on"]
            if not name:
                continue
            tid = _lc_topic_id(name)
            uid_items[uid].append({"type": "lc", "lc_id": tid, "name": name, "completed_on": co})
            lookup_pairs.add(("lc", tid))

        score_map = get_scores_for_items(list(lookup_pairs))

        from datetime import date
        start_of_month = date.today().replace(day=1)

        for uid in uncached_uids:
            ur   = {ax: 0.0 for ax in AXES}
            prev = {ax: 0.0 for ax in AXES}
            seen_lc: set[int] = set()
            for item in uid_items[uid]:
                if item["type"] == "course" and item.get("cat_id"):
                    sc = score_map.get(("course", int(item["cat_id"])))
                elif item["type"] == "cert" and item.get("cert_id"):
                    sc = score_map.get(("cert", int(item["cert_id"])))
                elif item["type"] == "lc" and item.get("lc_id"):
                    tid = int(item["lc_id"])
                    if tid in seen_lc:
                        continue
                    seen_lc.add(tid)
                    sc = score_map.get(("lc", tid))
                else:
                    sc = None
                if sc is None:
                    sc = _keyword_scores(item["name"])
                co = item.get("completed_on")
                co_date = co.date() if co and hasattr(co, "date") else co
                for ax in AXES:
                    v = float(sc.get(ax, 0))
                    ur[ax] += v
                    if co_date and co_date < start_of_month:
                        prev[ax] += v

            this_norm = {ax: round(min(100.0, (ur[ax]   / MASTERY_THRESHOLD) ** MASTERY_POWER * 100), 1) for ax in AXES}
            last_norm = {ax: round(min(100.0, (prev[ax] / MASTERY_THRESHOLD) ** MASTERY_POWER * 100), 1) for ax in AXES}
            delta     = round(sum(this_norm[AXES[i]] - last_norm[AXES[i]] for i in range(5)) / 5)

            raw_scores[uid] = ur
            # Only persist to cache when every completion query succeeded. On a
            # transient DB failure the scores are all-zero and caching them would
            # poison classify_{uid} for 24h (and break the radar / AI proficiency).
            if queries_ok:
                cache_entry = {
                    "axes":        AXES,
                    "this_month":  [this_norm[ax] for ax in AXES],
                    "last_month":  [last_norm[ax] for ax in AXES],
                    "delta":       delta,
                    "scored_by":   "gpt",
                    "_raw_scores": ur,
                }
                set_cache(f"classify_{uid}", cache_entry, "gpt", ttl_hours=24)

    for uid, cached in cached_results.items():
        raw_scores[uid] = cached.get("_raw_scores", {ax: 0.0 for ax in AXES})

    all_uids = list(user_ids)
    result: dict[int, dict] = {}
    for uid in all_uids:
        ur = raw_scores.get(uid, {ax: 0.0 for ax in AXES})
        normed: dict[str, float] = {}
        for ax in AXES:
            normed[ax] = round(min(100.0, (ur[ax] / MASTERY_THRESHOLD) ** MASTERY_POWER * 100), 1)
        normed["_scored_by"] = cached_results.get(uid, {}).get("scored_by", "gpt")
        result[uid] = normed

    return result


def get_team_skill_radar(user_ids: list[int]) -> dict:
    """
    Team-averaged skill radar with two series (this month vs last month), for the
    manager "Your Team" page. Averages each axis across the team.

    Reuses the per-user classify_{uid} cache (which already holds this_month /
    last_month arrays). get_team_skill_scores() is called first purely to warm
    those caches for any cold uids in one batch — we then read the two-series
    arrays back and average them. Returns:
        {"axes": AXES, "this_month": [...5], "last_month": [...5]}
    """
    from nova_db.gpt_cache import get_cache

    if not user_ids:
        return {"axes": AXES, "this_month": [0.0] * len(AXES), "last_month": [0.0] * len(AXES)}

    # Warm classify_{uid} for any cold uids (side effect of this call).
    try:
        get_team_skill_scores(user_ids)
    except Exception as exc:
        logger.warning("get_team_skill_radar: warm failed: %s", exc)

    n = len(AXES)
    this_sum = [0.0] * n
    last_sum = [0.0] * n
    counted = 0
    for uid in user_ids:
        c = get_cache(f"classify_{uid}")
        if not c:
            continue
        res = c["result"]
        tm = res.get("this_month") or [0.0] * n
        lm = res.get("last_month") or [0.0] * n
        for i in range(n):
            this_sum[i] += float(tm[i]) if i < len(tm) else 0.0
            last_sum[i] += float(lm[i]) if i < len(lm) else 0.0
        counted += 1

    if counted == 0:
        return {"axes": AXES, "this_month": [0.0] * n, "last_month": [0.0] * n}

    return {
        "axes":       AXES,
        "this_month": [round(this_sum[i] / counted, 1) for i in range(n)],
        "last_month": [round(last_sum[i] / counted, 1) for i in range(n)],
    }


def calculate_ai_proficiency(user_id: int, conn=None) -> float:
    radar = calculate_skill_radar(user_id)
    ai_idx = AXES.index("AI")
    return round(float(radar["this_month"][ai_idx]), 1)


def get_team_ai_proficiency(manager_user_id: int, conn=None) -> dict:
    """Returns count and % of direct reports who are AI proficient."""
    reports = query(
        """
        SELECT DISTINCT user_id
        FROM   classmate.dim_classmate_employee_profile
        WHERE  manager    = ?
          AND  is_active  = 1
          AND  is_deleted = 0
        """,
        (manager_user_id,),
    )
    if not reports:
        return {"count": 0, "pct": 0.0, "total": 0}

    uids = [r["user_id"] for r in reports]
    team_scores = get_team_skill_scores(uids)
    ai_idx = AXES.index("AI")
    threshold = settings.ai_proficiency_min_score

    proficient = sum(
        1 for uid in uids
        if team_scores.get(uid, {}).get("AI", 0.0) >= threshold
    )
    total = len(uids)
    return {
        "count": proficient,
        "pct":   round(proficient / total * 100, 1) if total else 0.0,
        "total": total,
    }
