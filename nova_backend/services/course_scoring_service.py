"""
services/course_scoring_service.py
Background job that scores every course and certificate in the catalogue
across 5 skill verticals using GPT, then stores results in SQLite.
Called once at startup (incremental — only unscored items are sent to GPT).
"""

import json
import logging
import zlib
from datetime import datetime, timezone

from core.database import query
from core.config import settings
from nova_db.course_scores import (
    init_course_scores_table,
    get_scored_pairs,
    upsert_scores,
    get_scored_count,
)

logger = logging.getLogger(__name__)


def lc_topic_id(topic: str) -> int:
    """Stable integer ID for an LC topic string (CRC32, always positive)."""
    return zlib.crc32(topic.encode("utf-8", errors="replace")) & 0x7FFFFFFF


_SYSTEM_PROMPT = (
    "You are a learning content expert. For each item, score its relevance to 5 skill "
    "verticals on a scale of 0-100:\n"
    "AI: machine learning, LLMs, generative AI, NLP, data science, neural networks\n"
    "Cloud: AWS/Azure/GCP, DevOps, containers, Kubernetes, serverless, infrastructure\n"
    "Frontend: React/Angular/Vue, HTML/CSS, UX/UI design, mobile, web accessibility\n"
    "Backend: Python/Java/Node, APIs, databases, microservices, GraphQL, .NET\n"
    "Data: analytics, Power BI/Tableau, ETL, data engineering, SQL, warehousing\n\n"
    "Score guide: 80-100 = primarily this vertical, 50-79 = significant content, "
    "20-49 = some relevance, 1-19 = minimal, 0 = no relevance at all.\n"
    "A purely Frontend course (e.g. 'React Fundamentals') should score 0 for AI, Cloud, Backend, and Data.\n"
    "Return ONLY valid JSON, no markdown, no explanation:\n"
    '{"scores":[{"id":<int>,"ai":<int>,"cloud":<int>,"frontend":<int>,"backend":<int>,"data":<int>},...]}'
)

_BATCH_SIZE = 25


def _keyword_fallback_scores(items: list[dict]) -> list[dict]:
    from services.skill_service import _classify
    rows = []
    for item in items:
        matched = _classify(item["name"])
        rows.append({
            "item_type": item["type"],
            "item_id":   item["id"],
            "item_name": item["name"],
            "ai":       70 if matched == "AI"       else 0,
            "cloud":    70 if matched == "Cloud"    else 0,
            "frontend": 70 if matched == "Frontend" else 0,
            "backend":  70 if matched == "Backend"  else 0,
            "data":     70 if matched == "Data"     else 0,
        })
    return rows


def _gpt_score_batch(items: list[dict]) -> list[dict]:
    if not items:
        return []
    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            api_key=settings.openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.openai_api_version,
        )
        user_lines = "\n".join(
            f"{i+1}. (id:{item['id']}) {item['name']}"
            for i, item in enumerate(items)
        )
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": f"Score these learning items:\n{user_lines}"},
            ],
            temperature=0.0,
            max_tokens=1500,
            timeout=15,
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)
        scores_list = parsed.get("scores")
        if not isinstance(scores_list, list):
            raise ValueError("GPT response missing 'scores' list")

        id_to_scores: dict[int, dict] = {}
        for entry in scores_list:
            if isinstance(entry, dict) and "id" in entry:
                item_id = int(entry["id"])
                id_to_scores[item_id] = {
                    "ai":       max(0, min(100, int(entry.get("ai",       0)))),
                    "cloud":    max(0, min(100, int(entry.get("cloud",    0)))),
                    "frontend": max(0, min(100, int(entry.get("frontend", 0)))),
                    "backend":  max(0, min(100, int(entry.get("backend",  0)))),
                    "data":     max(0, min(100, int(entry.get("data",     0)))),
                }

        rows = []
        for item in items:
            sc = id_to_scores.get(item["id"])
            if sc is None:
                sc = _keyword_fallback_scores([item])[0]
                sc.pop("item_type", None); sc.pop("item_id", None); sc.pop("item_name", None)
            rows.append({
                "item_type": item["type"],
                "item_id":   item["id"],
                "item_name": item["name"],
                **sc,
            })
        return rows

    except Exception as exc:
        logger.warning("GPT scoring batch failed (%d items) — keyword fallback: %s", len(items), exc)
        return _keyword_fallback_scores(items)


def _fetch_catalogue_rows() -> tuple:
    """The 3 raw catalogue sources: courses, certificates, LC topics. Each
    query has its own try/except (defaults to []) so one source failing
    doesn't block scoring the others."""
    try:
        course_rows = query(
            """
            SELECT id, name
            FROM dim_classmate_second_level_category
            WHERE is_deleted = 0
              AND is_active  = 1
              AND etl_isactive = 1
            """
        )
    except Exception as exc:
        logger.error("score_all_courses: could not fetch courses: %s", exc)
        course_rows = []

    try:
        cert_rows = query(
            """
            SELECT id, certificate_name AS name
            FROM dim_classmate_certificate
            WHERE is_deleted   = 0
              AND etl_isactive = 1
            """
        )
    except Exception as exc:
        logger.error("score_all_courses: could not fetch certs: %s", exc)
        cert_rows = []

    try:
        lc_rows = query(
            """
            SELECT DISTINCT topic AS name
            FROM fact_classmate_learning_credit
            WHERE is_deleted = 0
              AND topic IS NOT NULL
              AND topic != ''
              AND (self_study_id IS NOT NULL
                   OR session_id IS NOT NULL
                   OR recorded_training_id IS NOT NULL)
            """
        )
    except Exception as exc:
        logger.error("score_all_courses: could not fetch LC items: %s", exc)
        lc_rows = []

    return course_rows, cert_rows, lc_rows


def _normalize_catalogue_items(course_rows, cert_rows, lc_rows) -> list:
    """Merge the 3 catalogue sources into one uniform {type, id, name} list."""
    all_items: list[dict] = []
    for r in course_rows:
        if r.get("id") and r.get("name"):
            all_items.append({"type": "course", "id": int(r["id"]), "name": r["name"]})
    for r in cert_rows:
        if r.get("id") and r.get("name"):
            all_items.append({"type": "cert", "id": int(r["id"]), "name": r["name"]})
    for r in lc_rows:
        name = r.get("name") or ""
        if name:
            all_items.append({"type": "lc", "id": lc_topic_id(name), "name": name})
    return all_items


def _diff_unscored_items(all_items: list) -> list:
    """Items not yet present in course_vertical_scores."""
    already_scored = get_scored_pairs()
    unscored = [
        item for item in all_items
        if (item["type"], item["id"]) not in already_scored
    ]
    logger.info(
        "score_all_courses: %d total items, %d already scored, %d to score",
        len(all_items), len(already_scored), len(unscored),
    )
    return unscored


def _score_and_persist_batches(unscored: list) -> int:
    """Scores `unscored` in _BATCH_SIZE chunks via GPT (with keyword fallback)
    and persists each batch immediately. Returns the total newly-scored count."""
    total_scored = 0
    for batch_start in range(0, len(unscored), _BATCH_SIZE):
        batch = unscored[batch_start : batch_start + _BATCH_SIZE]
        rows = _gpt_score_batch(batch)
        if rows:
            upsert_scores(rows)
            total_scored += len(rows)
            logger.info(
                "score_all_courses: scored batch %d-%d (%d items so far)",
                batch_start, batch_start + len(batch), total_scored,
            )

    logger.info(
        "score_all_courses: complete — %d new items scored (total in db: %d)",
        total_scored, get_scored_count(),
    )
    return total_scored


def _invalidate_trend_cache() -> None:
    """Clear the cached ai_proficiency_trend result so it recomputes with the
    newly scored items. Uses a direct sqlite3 connection rather than
    gpt_cache.py's clear_by_prefix (which is prefix-match, not this exact-key
    delete) — kept exactly as-is, not swapped, as part of this refactor."""
    try:
        import sqlite3 as _sqlite3
        _db = settings.nova_local_db_path
        with _sqlite3.connect(str(_db)) as _c:
            _c.execute("DELETE FROM gpt_cache WHERE cache_key='ai_proficiency_trend'")
            _c.commit()
        logger.info("score_all_courses: ai_proficiency_trend cache cleared for recompute")
    except Exception as exc:
        logger.warning("score_all_courses: could not clear trend cache: %s", exc)


def score_all_courses() -> None:
    init_course_scores_table()

    # Production backstop: when scoring is disabled (NOVA_COURSE_SCORING_ENABLED=
    # false), never call GPT. This guarantees a missing/empty nova_local.db can
    # never silently trigger the ~8h full-catalogue rescore in production — the
    # app just serves whatever scores are already seeded in the DB.
    if not settings.nova_course_scoring_enabled:
        logger.warning(
            "score_all_courses: scoring DISABLED (NOVA_COURSE_SCORING_ENABLED=false) — "
            "skipping; %d courses already scored in nova_local.db",
            get_scored_count(),
        )
        return

    logger.info("score_all_courses: starting catalogue scoring job")

    course_rows, cert_rows, lc_rows = _fetch_catalogue_rows()
    all_items = _normalize_catalogue_items(course_rows, cert_rows, lc_rows)
    unscored = _diff_unscored_items(all_items)

    if not unscored:
        logger.info("score_all_courses: nothing new to score")
        return

    total_scored = _score_and_persist_batches(unscored)

    if total_scored > 0:
        _invalidate_trend_cache()
