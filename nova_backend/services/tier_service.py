"""
services/tier_service.py
Tier ranking and score calculation for Nova.
"""

import logging
from core.database import query
from core.config import settings

logger = logging.getLogger(__name__)


def _percentile_to_tier(percentile: float) -> tuple[str, str]:
    t = settings.tier_thresholds
    if percentile <= t["platinum"]:
        return "platinum", "platinum"
    elif percentile <= t["diamond"]:
        return "diamond", "platinum"
    elif percentile <= t["gold"]:
        return "gold", "diamond"
    elif percentile <= t["silver"]:
        return "silver", "gold"
    elif percentile <= t["bronze"]:
        return "bronze", "silver"
    else:
        return "starter", "bronze"


def _tier_progress(percentile: float) -> int:
    t = settings.tier_thresholds
    bands = [
        (t["platinum"], 0),
        (t["diamond"],  t["platinum"]),
        (t["gold"],     t["diamond"]),
        (t["silver"],   t["gold"]),
        (t["bronze"],   t["silver"]),
        (100,           t["bronze"]),
    ]
    for upper, lower in bands:
        if percentile <= upper:
            span = upper - lower
            if span == 0:
                return 100
            progress = (upper - percentile) / span * 100
            return max(0, min(100, int(progress)))
    return 0


_TIER_THRESHOLDS = [
    (500, "platinum", "platinum"),
    (200, "diamond",  "platinum"),
    (100, "gold",     "diamond"),
    (50,  "silver",   "gold"),
    (20,  "bronze",   "silver"),
    (0,   "starter",  "bronze"),
]

# ── Single source of truth for tiers ─────────────────────────────────────────
# Every tier is computed in ONE place (compute_and_cache_tiers) and stored in the
# tier_{uid} cache. Both the employee dashboard (calculate_tier) and the manager
# view (_batch_tier_map) are pure cache reads — they never recompute. This is what
# guarantees the two views always show the same tier for the same user.
#
# tier_score = credits*0.30 + skill*0.35 + consistency*0.20 + recency*0.15
# This weighting + per-component normalisation must stay identical to
# refresh_tier_scores_cache() (which builds the ranking population), or cached
# tiers would drift from the population they're ranked against.

# TTL long enough to survive comfortably until the next nightly 3AM rebuild.
_TIER_CACHE_TTL_HOURS = 26


def _global_avg_30d() -> float:
    """Company-wide average 30-day credits — the recency denominator. Written by
    refresh_tier_scores_cache(); falls back to 1.0 when the cache is cold."""
    from nova_db.gpt_cache import get_cache
    c = get_cache("company_avg_30d_credits")
    if c:
        try:
            v = float(c["result"].get("avg") or 0.0)
            return v if v > 0 else 1.0
        except Exception:
            return 1.0
    return 1.0


def _batch_tier_inputs(uids: list[int]) -> tuple[dict, dict, dict]:
    """Three batched Fabric queries for a set of users: all-time completed
    credits, 30-day recency credits, and 90-day distinct active days. Users with
    no rows default to zero downstream."""
    credits_map: dict[int, float] = {}
    recency_map: dict[int, float] = {}
    active_map:  dict[int, int]   = {}
    if not uids:
        return credits_map, recency_map, active_map

    ph = ",".join("?" * len(uids))
    try:
        rows = query(
            f"SELECT user_id, ISNULL(SUM(learning_credits),0) AS tc "
            f"FROM classmate.vw_classmate_trainings "
            f"WHERE user_id IN ({ph}) AND status=4052 GROUP BY user_id",
            tuple(uids),
        )
        credits_map = {int(r["user_id"]): float(r["tc"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("tier inputs: credits query failed: %s", exc)
    try:
        rows = query(
            f"SELECT user_id, ISNULL(SUM(value),0) AS c30 "
            f"FROM classmate.fact_classmate_learning_credit "
            f"WHERE user_id IN ({ph}) AND is_deleted=0 "
            f"  AND credit_date >= DATEADD(day,-30,GETDATE()) GROUP BY user_id",
            tuple(uids),
        )
        recency_map = {int(r["user_id"]): float(r["c30"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("tier inputs: recency query failed: %s", exc)
    try:
        # 90-day window ending today inclusive — matches calculate_streak's count.
        rows = query(
            f"""SELECT user_id, COUNT(DISTINCT activity_date) AS ad90
            FROM (
                SELECT user_id, CAST(credit_date AS DATE) AS activity_date
                FROM classmate.fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0 AND duration>0
                UNION
                SELECT user_id, CAST(modified_on AS DATE)
                FROM classmate.fact_classmate_user_skill_status
                WHERE user_id IN ({ph}) AND is_deleted=0 AND is_active=1
                UNION
                SELECT user_id, CAST(attended_date AS DATE)
                FROM classmate.fact_classmate_self_study
                WHERE user_id IN ({ph}) AND status=2 AND is_deleted=0
            ) src
            WHERE activity_date IS NOT NULL
              AND activity_date >= CAST(DATEADD(day,-89,GETDATE()) AS DATE)
              AND activity_date <= CAST(GETDATE() AS DATE)
            GROUP BY user_id""",
            tuple(uids) * 3,
        )
        active_map = {int(r["user_id"]): int(r["ad90"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("tier inputs: active-days query failed: %s", exc)

    return credits_map, recency_map, active_map


def compute_and_cache_tiers(uids, population_scores=None, skill_norm=None,
                            inputs=None) -> dict:
    """THE single place that computes a tier and writes tier_{uid}.

    Computes the full tier dict for each uid, ranks its tier_score against the
    population (user_tier_scores), and caches it (26h TTL). Returns {uid: dict}.

    Callers may pass `population_scores`, `skill_norm`, and `inputs` (a
    (credits_map, recency_map, active_map) tuple) to avoid re-querying — e.g.
    refresh_tier_scores_cache passes everything it already computed so this adds
    no extra Fabric load."""
    from nova_db.gpt_cache import set_cache
    from nova_db.tier_scores import get_all_tier_scores
    from services.skill_service import get_team_skill_scores

    uids = [int(u) for u in uids]
    if not uids:
        return {}

    if population_scores is None:
        population_scores = get_all_tier_scores()
    sorted_scores = sorted(population_scores.values(), reverse=True)
    total_pop = len(sorted_scores)

    if inputs is not None:
        credits_map, recency_map, active_map = inputs
    else:
        credits_map, recency_map, active_map = _batch_tier_inputs(uids)
    avg = _global_avg_30d()

    if skill_norm is None:
        try:
            skill_norm = get_team_skill_scores(uids)
        except Exception as exc:
            logger.warning("compute_and_cache_tiers: skill scores failed: %s", exc)
            skill_norm = {}

    out: dict = {}
    for uid in uids:
        tc   = credits_map.get(uid, 0.0)
        u30  = recency_map.get(uid, 0.0)
        ad90 = active_map.get(uid, 0)

        credits_score     = round(min(tc / 500 * 100, 100), 1)
        consistency_score = round(ad90 / 90 * 100, 1)
        recency_score     = round(min(u30 / avg / 3 * 100, 100), 1)
        if uid in skill_norm:
            skill_score = round(
                sum(v for k, v in skill_norm[uid].items() if not k.startswith("_")) / 5, 1)
        else:
            skill_score = 0.0

        tier_score = (
            credits_score * 0.30
            + skill_score * 0.35
            + consistency_score * 0.20
            + recency_score * 0.15
        )

        if total_pop > 0:
            rank = sum(1 for s in sorted_scores if s > tier_score)
            approx_pct = rank / total_pop * 100
        else:
            approx_pct = 50.0

        current_tier, next_tier = _percentile_to_tier(approx_pct)
        scored_by = skill_norm.get(uid, {}).get("_scored_by", "keywords")

        result = {
            "current_tier":      current_tier,
            "next_tier":         next_tier,
            "tier_progress":     _tier_progress(approx_pct),
            "percentile":        round(approx_pct, 1),
            "total_credits":     round(tc, 1),
            "tier_score":        round(tier_score, 1),
            "credits_score":     credits_score,
            "skill_score":       skill_score,
            "consistency_score": consistency_score,
            "recency_score":     recency_score,
            "scored_by":         scored_by,
        }
        set_cache(f"tier_{uid}", result, "batch", ttl_hours=_TIER_CACHE_TTL_HOURS)
        out[uid] = result

    return out


def populate_missing_tiers() -> int:
    """Compute tiers for every active user that lacks a tier_{uid} cache entry,
    in one batch. Returns the number populated. Rare after the startup/nightly
    refresh warms the cache (only brand-new users or a manual cache clear)."""
    from nova_db.gpt_cache import get_cache

    try:
        from routers.manager import _get_all_active_uids
        all_uids = _get_all_active_uids()
    except Exception as exc:
        logger.warning("populate_missing_tiers: could not list active users: %s", exc)
        from nova_db.tier_scores import get_all_tier_scores
        all_uids = list(get_all_tier_scores().keys())

    missing = [uid for uid in all_uids if not get_cache(f"tier_{uid}")]
    if not missing:
        return 0
    logger.info("populate_missing_tiers: computing %d missing tiers", len(missing))
    compute_and_cache_tiers(missing)
    return len(missing)


_STARTER_TIER = {
    "current_tier": "starter", "next_tier": "bronze", "tier_progress": 0,
    "percentile": 100.0, "total_credits": 0.0, "tier_score": 0.0,
    "credits_score": 0.0, "skill_score": 0.0, "consistency_score": 0.0,
    "recency_score": 0.0, "scored_by": "keywords",
}


def calculate_tier(user_id: int) -> dict:
    """Pure cache read for the employee dashboard. Tiers are computed in batch
    (refresh_tier_scores_cache / populate_missing_tiers), so the employee and
    manager views always read the identical value. On a miss, populate all
    not-yet-cached users, then read."""
    from nova_db.gpt_cache import get_cache

    cached = get_cache(f"tier_{user_id}")
    if cached:
        return cached["result"]

    populate_missing_tiers()
    cached = get_cache(f"tier_{user_id}")
    if cached:
        return cached["result"]

    # Still missing (e.g. user not in the active-employee list) — compute just this one.
    out = compute_and_cache_tiers([user_id])
    return out.get(user_id, dict(_STARTER_TIER))


def get_all_user_tiers() -> dict:
    all_credits_rows = query(
        """
        SELECT user_id, SUM(learning_credits) AS credits
        FROM   classmate.vw_classmate_trainings
        WHERE  status = 4052
        GROUP BY user_id
        """,
    )
    if not all_credits_rows:
        return {}

    result = {}
    for r in all_credits_rows:
        uid = r["user_id"]
        credits = float(r["credits"] or 0)
        tier = "starter"
        for threshold, t, _ in _TIER_THRESHOLDS:
            if credits >= threshold:
                tier = t
                break
        result[uid] = tier
    return result
