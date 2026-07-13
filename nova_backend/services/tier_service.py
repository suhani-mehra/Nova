"""
services/tier_service.py
Tier ranking and score calculation for Nova.
"""

import logging
from datetime import date
from core.database import query
from core.config import settings

logger = logging.getLogger(__name__)


# ── Month windowing ───────────────────────────────────────────────────────────
# The tier is a MONTHLY competition: credits/recency/consistency window to a
# calendar month (reset on the 1st); skill stays all-time. A `target_month` is a
# date on the 1st of the month being scored; callers default to the current month.

def _current_month() -> date:
    return date.today().replace(day=1)


def _month_bounds(target_month: date) -> tuple[str, str, int]:
    """(start_iso, end_excl_iso, days_in_month) for the calendar month that
    `target_month` falls in. Bounds are passed to SQL as `>= start AND < end`."""
    start = target_month.replace(day=1)
    if start.month == 12:
        end_excl = start.replace(year=start.year + 1, month=1)
    else:
        end_excl = start.replace(month=start.month + 1)
    return start.isoformat(), end_excl.isoformat(), (end_excl - start).days


def _month_str(d: date) -> str:
    return d.strftime("%Y-%m")


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


def _batch_tier_inputs(uids: list[int], target_month: date | None = None) -> tuple[dict, dict, dict]:
    """Three batched Fabric queries for a set of users, all windowed to the
    calendar month `target_month` (defaults to the current month): completed
    credits, recency credits, and distinct active days within that month. Users
    with no rows default to zero downstream. Skill is NOT fetched here — it stays
    all-time (see compute_and_cache_tiers)."""
    credits_map: dict[int, float] = {}
    recency_map: dict[int, float] = {}
    active_map:  dict[int, int]   = {}
    if not uids:
        return credits_map, recency_map, active_map

    start, end_excl, _ = _month_bounds(target_month or _current_month())
    ph = ",".join("?" * len(uids))
    try:
        rows = query(
            f"SELECT user_id, COALESCE(SUM(learning_credits),0) AS tc "
            f"FROM vw_classmate_trainings "
            f"WHERE user_id IN ({ph}) AND status=4052 "
            f"  AND completed_on >= ? AND completed_on < ? GROUP BY user_id",
            tuple(uids) + (start, end_excl),
        )
        credits_map = {int(r["user_id"]): float(r["tc"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("tier inputs: credits query failed: %s", exc)
    try:
        rows = query(
            f"SELECT user_id, COALESCE(SUM(value),0) AS c30 "
            f"FROM fact_classmate_learning_credit "
            f"WHERE user_id IN ({ph}) AND is_deleted=0 "
            f"  AND credit_date >= ? AND credit_date < ? GROUP BY user_id",
            tuple(uids) + (start, end_excl),
        )
        recency_map = {int(r["user_id"]): float(r["c30"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("tier inputs: recency query failed: %s", exc)
    try:
        # Distinct active days WITHIN the target month.
        rows = query(
            f"""SELECT user_id, COUNT(DISTINCT activity_date) AS ad90
            FROM (
                SELECT user_id, date(credit_date) AS activity_date
                FROM fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0 AND duration>0
                UNION
                SELECT user_id, date(modified_on)
                FROM fact_classmate_user_skill_status
                WHERE user_id IN ({ph}) AND is_deleted=0 AND is_active=1
                UNION
                SELECT user_id, date(attended_date)
                FROM fact_classmate_self_study
                WHERE user_id IN ({ph}) AND status=2 AND is_deleted=0
            ) src
            WHERE activity_date IS NOT NULL
              AND activity_date >= ? AND activity_date < ?
            GROUP BY user_id""",
            tuple(uids) * 3 + (start, end_excl),
        )
        active_map = {int(r["user_id"]): int(r["ad90"] or 0) for r in rows}
    except Exception as exc:
        logger.warning("tier inputs: active-days query failed: %s", exc)

    return credits_map, recency_map, active_map


def compute_and_cache_tiers(uids, population_scores=None, skill_norm=None,
                            inputs=None, target_month=None, write_cache=True,
                            recency_avg=None) -> dict:
    """THE single place that computes a tier and writes tier_{uid}.

    Computes the MONTHLY tier dict for each uid (credits/recency/consistency
    windowed to `target_month`, skill all-time), ranks its tier_score against the
    population (user_tier_scores), and caches it (26h TTL). Returns {uid: dict}.

    Callers may pass `population_scores`, `skill_norm`, and `inputs` (a
    (credits_map, recency_map, active_map) tuple) to avoid re-querying — e.g.
    refresh_tier_scores_cache passes everything it already computed so this adds
    no extra Fabric load. `target_month` defaults to the current month; the badge
    job passes a prior month with `write_cache=False` so it never overwrites the
    live tier_{uid} cache."""
    from nova_db.gpt_cache import set_cache
    from nova_db.tier_scores import get_all_tier_scores
    from services.skill_service import get_team_skill_scores

    uids = [int(u) for u in uids]
    if not uids:
        return {}

    tm = target_month or _current_month()
    _, _, days_in_month = _month_bounds(tm)

    if population_scores is None:
        population_scores = get_all_tier_scores()
    sorted_scores = sorted(population_scores.values(), reverse=True)
    total_pop = len(sorted_scores)

    if inputs is not None:
        credits_map, recency_map, active_map = inputs
    else:
        credits_map, recency_map, active_map = _batch_tier_inputs(uids, tm)
    # Recency denominator must match the population being ranked. The badge job
    # passes the prior month's average explicitly; the live path uses the cached
    # (current-month) average written by refresh_tier_scores_cache.
    avg = recency_avg if recency_avg is not None else _global_avg_30d()

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

        # Monthly normalisation: credits vs a monthly target, consistency vs the
        # days in the month, recency vs the (monthly) company average.
        credits_score     = round(min(tc / settings.monthly_credit_target * 100, 100), 1)
        consistency_score = round(min(ad90 / days_in_month * 100, 100), 1)
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
        if write_cache:
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


def award_monthly_badges(target_month: date, awarded_at: str | None = None) -> int:
    """Award each active learner a badge equal to the tier they ENDED
    `target_month` with (a completed calendar month). 'starter' is never awarded.
    Idempotent — user_badges has UNIQUE(user_id, month), so re-runs are no-ops.

    Computes the month's FINAL tiers via the shared population helper with
    write_cache=False, so it never overwrites the live (current-month) tier cache."""
    from nova_db.tier_scores import _compute_population_scores
    from nova_db.badges import award_badge

    computed = _compute_population_scores(target_month)
    if computed is None:
        logger.warning("award_monthly_badges: no population for %s", _month_str(target_month))
        return 0
    all_uids, scores, skill_map, inputs, monthly_avg = computed

    tiers = compute_and_cache_tiers(
        all_uids,
        population_scores=scores,
        skill_norm=skill_map,
        inputs=inputs,
        target_month=target_month,
        write_cache=False,
        recency_avg=monthly_avg,
    )

    month = _month_str(target_month)
    at = awarded_at or date.today().isoformat()
    awarded = 0
    for uid, td in tiers.items():
        tier = td.get("current_tier")
        if tier and tier != "starter":
            award_badge(uid, tier, month, at)
            awarded += 1
    logger.info("award_monthly_badges %s: awarded %d non-starter badges", month, awarded)
    return awarded
