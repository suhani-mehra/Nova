"""
nova_db/tier_scores.py
SQLite cache for population-wide composite tier scores.
Used by tier_service.py to do percentile ranking against all users
without expensive per-user Fabric queries at request time.
"""

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "nova_local.db"
_CACHE_TTL_HOURS = 24
_refresh_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_tier_scores_table() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_tier_scores (
                user_id    INTEGER PRIMARY KEY,
                tier_score REAL    NOT NULL,
                updated_at TEXT    NOT NULL
            )
        """)
        c.commit()


def get_tier_scores_age_hours() -> float | None:
    """Returns age of the cache in hours, or None if the table is empty."""
    with _conn() as c:
        row = c.execute("SELECT MAX(updated_at) AS ts FROM user_tier_scores").fetchone()
    if not row or not row["ts"]:
        return None
    try:
        updated = datetime.fromisoformat(row["ts"])
        return (datetime.now().replace(microsecond=0) - updated).total_seconds() / 3600
    except Exception:
        return None


def get_all_tier_scores() -> dict[int, float]:
    """Returns {user_id: tier_score} for the full cached population."""
    with _conn() as c:
        rows = c.execute("SELECT user_id, tier_score FROM user_tier_scores").fetchall()
    return {int(r["user_id"]): float(r["tier_score"]) for r in rows}


def upsert_tier_scores(scores: dict[int, float]) -> None:
    """Bulk upsert (INSERT OR REPLACE) all user tier scores with current UTC timestamp."""
    now = datetime.now().replace(microsecond=0).isoformat()
    rows = [(uid, score, now) for uid, score in scores.items()]
    with _conn() as c:
        c.executemany(
            "INSERT OR REPLACE INTO user_tier_scores (user_id, tier_score, updated_at) VALUES (?,?,?)",
            rows,
        )
        c.commit()
    logger.info("Upserted %d user tier scores into SQLite", len(rows))


def refresh_tier_scores_cache(force: bool = False) -> None:
    """
    Batch-compute composite tier_score for every active user and cache in SQLite.
    Skips if cache is < TTL hours old, unless force=True.
    Uses a threading.Lock to prevent concurrent refreshes.
    """
    if not force:
        age = get_tier_scores_age_hours()
        if age is not None and age < _CACHE_TTL_HOURS:
            logger.info("Tier score cache is %.1fh old — skipping refresh", age)
            return

    if not _refresh_lock.acquire(blocking=False):
        logger.info("Tier score refresh already running — skipping")
        return

    try:
        logger.info("Starting population-wide tier score refresh...")
        from core.database import query
        from services.skill_service import get_team_skill_scores

        # Query 1: All users' total completed credits
        credit_rows = query(
            """
            SELECT user_id, ISNULL(SUM(learning_credits), 0) AS tc
            FROM classmate.vw_classmate_trainings
            WHERE status = 4052
            GROUP BY user_id
            """
        )
        credits_map: dict[int, float] = {int(r["user_id"]): float(r["tc"] or 0) for r in credit_rows}

        if not credits_map:
            logger.warning("Tier score refresh: no credit rows returned — aborting")
            return

        all_uids = list(credits_map.keys())

        # Query 2: All users' 30-day recency credits
        recency_rows = query(
            """
            SELECT user_id, ISNULL(SUM(value), 0) AS credits_30d
            FROM classmate.fact_classmate_learning_credit
            WHERE is_deleted = 0
              AND credit_date >= DATEADD(day,-30,GETDATE())
            GROUP BY user_id
            """
        )
        recency_map: dict[int, float] = {int(r["user_id"]): float(r["credits_30d"] or 0) for r in recency_rows}

        # Compute global average 30-day recency (across all users who appear in credits_map)
        all_recency = [recency_map.get(uid, 0.0) for uid in all_uids]
        global_avg_30d = sum(all_recency) / max(len(all_recency), 1)
        if global_avg_30d == 0:
            global_avg_30d = 1.0

        # Query 3: All users' active days in last 90 (batch streak, single query)
        consistency_rows = query(
            """
            SELECT user_id, COUNT(DISTINCT CAST(activity_date AS DATE)) AS active_days_90
            FROM (
                SELECT user_id, credit_date AS activity_date
                FROM classmate.fact_classmate_learning_credit
                WHERE is_deleted = 0 AND duration > 0
                  AND credit_date >= DATEADD(day,-90,GETDATE())
                UNION
                SELECT user_id, CAST(modified_on AS DATE)
                FROM classmate.fact_classmate_user_skill_status
                WHERE is_deleted = 0 AND is_active = 1
                  AND modified_on >= DATEADD(day,-90,GETDATE())
                UNION
                SELECT user_id, attended_date
                FROM classmate.fact_classmate_self_study
                WHERE status = 2 AND is_deleted = 0
                  AND attended_date >= DATEADD(day,-90,GETDATE())
            ) src
            WHERE activity_date IS NOT NULL
            GROUP BY user_id
            """
        )
        consistency_map: dict[int, int] = {int(r["user_id"]): int(r["active_days_90"] or 0) for r in consistency_rows}

        # Query 4+: Skill scores for all users (batches 3 Fabric queries internally)
        logger.info("Computing skill scores for %d users...", len(all_uids))
        skill_map = get_team_skill_scores(all_uids)

        # Assemble composite tier_score per user in Python (no more per-user Fabric calls)
        scores: dict[int, float] = {}
        for uid in all_uids:
            tc             = credits_map.get(uid, 0.0)
            recency_30d    = recency_map.get(uid, 0.0)
            active_days_90 = consistency_map.get(uid, 0)

            credits_score     = round(min(tc / 500 * 100, 100), 1)
            consistency_score = round(active_days_90 / 90 * 100, 1)
            recency_score     = round(min(recency_30d / global_avg_30d * 50, 100), 1)

            if uid in skill_map:
                skill_vals = [v for k, v in skill_map[uid].items() if not k.startswith("_")]
                skill_score = round(sum(skill_vals) / max(len(skill_vals), 1), 1)
            else:
                skill_score = 50.0

            tier_score = (
                credits_score     * 0.30
                + skill_score     * 0.35
                + consistency_score * 0.20
                + recency_score   * 0.15
            )
            scores[uid] = round(tier_score, 2)

        upsert_tier_scores(scores)
        logger.info(
            "Tier score refresh complete — %d users, score range %.1f–%.1f",
            len(scores),
            min(scores.values()),
            max(scores.values()),
        )

    except Exception as exc:
        logger.error("Tier score refresh failed: %s", exc)
    finally:
        _refresh_lock.release()
