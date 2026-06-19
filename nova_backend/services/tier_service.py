"""
services/tier_service.py
Tier ranking and score calculation for Nova.
"""

import logging
from core.database import query
from core.config import settings

logger = logging.getLogger(__name__)

_DEDUP_CTE_BODY = """
    WITH latest_profiles AS (
        SELECT *,
               ROW_NUMBER() OVER (
                 PARTITION BY user_id
                 ORDER BY modified_on DESC
               ) AS rn
        FROM classmate.dim_classmate_employee_profile
        WHERE etl_isactive = 1
          AND is_active    = 1
          AND is_deleted   = 0
    )
"""


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

_TIER_BANDS = {
    "platinum": (500, 9999),
    "diamond":  (200, 500),
    "gold":     (100, 200),
    "silver":   (50,  100),
    "bronze":   (20,  50),
    "starter":  (0,   20),
}


def calculate_tier(user_id: int, conn=None) -> dict:
    # 1. Total credits from completed trainings
    tc_rows = query(
        """
        SELECT ISNULL(SUM(learning_credits), 0) AS total
        FROM classmate.vw_classmate_trainings
        WHERE user_id=? AND status=4052
        """,
        (user_id,),
    )
    tc = float(tc_rows[0]["total"] or 0) if tc_rows else 0.0
    credits_score = round(min(tc / 500 * 100, 100), 1)

    # 2. Get user's manager_user_id
    mgr_rows = query(
        _DEDUP_CTE_BODY + """
        SELECT manager FROM latest_profiles
        WHERE rn=1 AND user_id=?
        """,
        (user_id,),
    )
    manager_id = mgr_rows[0]["manager"] if mgr_rows else None

    # 3. Get teammates
    from core.queries import get_direct_reports
    reports = get_direct_reports(None, manager_id) if manager_id else []
    teammate_ids = [r["user_id"] for r in reports if r["user_id"] != user_id]
    team = teammate_ids + [user_id]

    # 4. Skill score via team normalisation
    try:
        from services.skill_service import get_team_skill_scores, AXES
        team_norm = get_team_skill_scores(team)
        if user_id in team_norm:
            skill_score = round(
                sum(v for k, v in team_norm[user_id].items()
                    if not k.startswith("_")) / 5,
                1,
            )
        else:
            skill_score = 50.0
    except Exception as exc:
        logger.warning("skill score failed for uid=%s: %s", user_id, exc)
        skill_score = 50.0

    # 5. Consistency score
    try:
        from services.streak_service import calculate_streak
        s = calculate_streak(user_id)
        consistency_score = round(s["active_days_last_90"] / 90 * 100, 1)
    except Exception as exc:
        logger.warning("streak failed for uid=%s: %s", user_id, exc)
        consistency_score = 0.0

    # 6. Recency score
    recency_rows = query(
        """
        SELECT ISNULL(SUM(value), 0) AS credits
        FROM classmate.fact_classmate_learning_credit
        WHERE user_id=? AND is_deleted=0
          AND credit_date >= DATEADD(day,-30,GETDATE())
        """,
        (user_id,),
    )
    user_recency = float(recency_rows[0]["credits"] or 0) if recency_rows else 0.0

    if teammate_ids:
        ph = ",".join("?" * len(teammate_ids))
        avg_rows = query(
            f"""
            SELECT AVG(s.credits) AS avg_c FROM (
                SELECT user_id, SUM(value) AS credits
                FROM classmate.fact_classmate_learning_credit
                WHERE user_id IN ({ph}) AND is_deleted=0
                  AND credit_date >= DATEADD(day,-30,GETDATE())
                GROUP BY user_id
            ) s
            """,
            tuple(teammate_ids),
        )
        avg = float(avg_rows[0]["avg_c"] or user_recency or 1.0) if avg_rows else (user_recency or 1.0)
    else:
        avg = user_recency or 1.0
    if avg == 0:
        avg = 1.0
    recency_score = round(min(user_recency / avg * 50, 100), 1)

    # 7. Tier score
    tier_score = (
        credits_score * 0.30
        + skill_score * 0.35
        + consistency_score * 0.20
        + recency_score * 0.15
    )

    # 8. Percentile from full population via vw_classmate_trainings
    all_rows = query(
        """
        SELECT user_id, SUM(learning_credits) AS tc
        FROM classmate.vw_classmate_trainings
        WHERE status=4052
        GROUP BY user_id
        """,
    )

    if all_rows:
        rank = sum(1 for r in all_rows if float(r["tc"] or 0) > tc)
        approx_pct = rank / max(len(all_rows), 1) * 100
    else:
        approx_pct = 50.0

    # 9. Tier from percentile
    current_tier, next_tier = _percentile_to_tier(approx_pct)
    tier_progress = _tier_progress(approx_pct)

    # 10. scored_by from cache
    try:
        from nova_db.gpt_cache import get_cache
        cached = get_cache(f"classify_{user_id}")
        scored_by = cached["scored_by"] if cached else "keywords"
    except Exception:
        scored_by = "keywords"

    return {
        "current_tier":      current_tier,
        "next_tier":         next_tier,
        "tier_progress":     tier_progress,
        "percentile":        round(approx_pct, 1),
        "total_credits":     round(tc, 1),
        "tier_score":        round(tier_score, 1),
        "credits_score":     credits_score,
        "skill_score":       skill_score,
        "consistency_score": consistency_score,
        "recency_score":     recency_score,
        "scored_by":         scored_by,
    }


def get_all_user_tiers(conn=None) -> dict:
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
