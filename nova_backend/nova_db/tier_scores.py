"""
nova_db/tier_scores.py
SQLite cache for population-wide composite tier scores.
Used by tier_service.py to do percentile ranking against all users
without expensive per-user Fabric queries at request time.
"""

import logging
import sqlite3
import threading
from datetime import datetime, date
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
    except Exception as exc:
        logger.warning("get_tier_scores_age_hours: failed to parse updated_at=%r: %s", row["ts"], exc)
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


def _compute_population_scores(target_month: date):
    """Compute the MONTHLY composite tier_score for the whole active-learner
    population, windowed to `target_month`. Skill stays all-time. Returns
    (all_uids, scores, skill_map, inputs, monthly_avg) or None if no population.

    Shared by refresh_tier_scores_cache (current month, live) and
    award_monthly_badges (a completed prior month) so the formula is literally
    identical in both paths (tier invariant #1)."""
    from core.database import query
    from services.skill_service import get_team_skill_scores
    from services.tier_service import _month_bounds, _score_tier_components

    start, end_excl, days_in_month = _month_bounds(target_month)

    # Population = all-time active learners (anyone who has ever completed training).
    # Kept STABLE regardless of month so early-month percentiles don't blow up;
    # month-zero users simply rank near the bottom (intended monthly reset).
    pop_rows = query(
        """
        SELECT user_id, COALESCE(SUM(learning_credits), 0) AS completed_credits
        FROM vw_classmate_trainings
        WHERE status = 4052
        GROUP BY user_id
        """
    )
    all_uids = [int(r["user_id"]) for r in pop_rows]
    if not all_uids:
        logger.warning("Tier population: no active learners returned — aborting")
        return None

    # Month-to-date completed credits (zero-filled for non-earners this month).
    mc_rows = query(
        """
        SELECT user_id, COALESCE(SUM(learning_credits), 0) AS completed_credits
        FROM vw_classmate_trainings
        WHERE status = 4052 AND completed_on >= ? AND completed_on < ?
        GROUP BY user_id
        """,
        (start, end_excl),
    )
    credits_map: dict[int, float] = {int(r["user_id"]): float(r["completed_credits"] or 0) for r in mc_rows}

    # Month-to-date recency credits + monthly company average.
    recency_rows = query(
        """
        SELECT user_id, COALESCE(SUM(value), 0) AS credits_m
        FROM fact_classmate_learning_credit
        WHERE is_deleted = 0 AND credit_date >= ? AND credit_date < ?
        GROUP BY user_id
        """,
        (start, end_excl),
    )
    recency_map: dict[int, float] = {int(r["user_id"]): float(r["credits_m"] or 0) for r in recency_rows}
    all_recency = [recency_map.get(uid, 0.0) for uid in all_uids]
    monthly_avg = sum(all_recency) / max(len(all_recency), 1)
    if monthly_avg == 0:
        monthly_avg = 1.0

    # Month-to-date distinct active days.
    consistency_rows = query(
        """
        SELECT user_id, COUNT(DISTINCT date(activity_date)) AS active_days
        FROM (
            SELECT user_id, credit_date AS activity_date
            FROM fact_classmate_learning_credit
            WHERE is_deleted = 0 AND duration > 0
              AND credit_date >= ? AND credit_date < ?
            UNION
            SELECT user_id, date(modified_on)
            FROM fact_classmate_user_skill_status
            WHERE is_deleted = 0 AND is_active = 1
              AND modified_on >= ? AND modified_on < ?
            UNION
            SELECT user_id, attended_date
            FROM fact_classmate_self_study
            WHERE status = 2 AND is_deleted = 0
              AND attended_date >= ? AND attended_date < ?
        ) src
        WHERE activity_date IS NOT NULL
        GROUP BY user_id
        """,
        (start, end_excl, start, end_excl, start, end_excl),
    )
    consistency_map: dict[int, int] = {int(r["user_id"]): int(r["active_days"] or 0) for r in consistency_rows}

    # Skill is ALL-TIME (long-term anchor) — unchanged.
    logger.info("Computing skill scores for %d users...", len(all_uids))
    skill_map = get_team_skill_scores(all_uids)

    # Composite score per user — uses the same formula as compute_and_cache_tiers.
    scores: dict[int, float] = {}
    for uid in all_uids:
        completed_credits = credits_map.get(uid, 0.0)
        recency_m         = recency_map.get(uid, 0.0)
        active_m          = consistency_map.get(uid, 0)
        skill_vals = [v for k, v in skill_map.get(uid, {}).items() if not k.startswith("_")]

        result = _score_tier_components(
            completed_credits, recency_m, active_m, days_in_month, monthly_avg, skill_vals)
        scores[uid] = round(result["tier_score"], 2)

    return all_uids, scores, skill_map, (credits_map, recency_map, consistency_map), monthly_avg


def refresh_tier_scores_cache(force: bool = False) -> None:
    """
    Batch-compute the MONTHLY composite tier_score for every active user and cache
    in SQLite, windowed to the CURRENT month. Skips if cache is < TTL hours old,
    unless force=True. Uses a threading.Lock to prevent concurrent refreshes.
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
        from services.tier_service import compute_and_cache_tiers, _current_month
        logger.info("Starting population-wide MONTHLY tier score refresh...")

        tm = _current_month()
        computed = _compute_population_scores(tm)
        if computed is None:
            return
        all_uids, scores, skill_map, inputs, monthly_avg = computed

        upsert_tier_scores(scores)

        # Cache the monthly company average so the lazy path (calculate_tier /
        # populate_missing_tiers) recomputes current-month tiers consistently.
        from nova_db.gpt_cache import set_cache
        set_cache("company_avg_30d_credits", {"avg": monthly_avg}, "computed", ttl_hours=25)
        logger.info("Cached company_avg_30d_credits (monthly): %.4f", monthly_avg)

        # Single source of truth: write the full tier_{uid} dict for every user,
        # ranked against the population we just built, for the current month.
        compute_and_cache_tiers(
            all_uids,
            population_scores=scores,
            skill_norm=skill_map,
            inputs=inputs,
            target_month=tm,
            recency_avg=monthly_avg,
        )
        logger.info("Cached full monthly tier dicts for %d users", len(all_uids))

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
