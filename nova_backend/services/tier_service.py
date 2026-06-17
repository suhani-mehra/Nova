"""
services/tier_service.py
Tier ranking and score calculation for Nova.
"""

from datetime import date, timedelta
from core.database import query
from core.config import settings


def _percentile_to_tier(percentile: float) -> tuple[str, str]:
    """Returns (current_tier, next_tier) for a given percentile rank."""
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
    """How far (0-100) the user is toward the next tier threshold."""
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
    user_credits_rows = query(
        """
        SELECT ISNULL(SUM(learning_credits), 0) AS total_credits
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        """,
        (user_id,),
    )
    user_credits = float(user_credits_rows[0]["total_credits"] or 0) if user_credits_rows else 0.0

    current_tier, next_tier = "starter", "bronze"
    for threshold, tier, nxt in _TIER_THRESHOLDS:
        if user_credits >= threshold:
            current_tier, next_tier = tier, nxt
            break

    lower, upper = _TIER_BANDS.get(current_tier, (0, 20))
    if current_tier == "platinum":
        progress = 100
    else:
        span = upper - lower
        progress = max(0, min(100, int((user_credits - lower) / span * 100))) if span > 0 else 0

    percentile = 50.0  # approximate — full scan not available without mv table

    credits_score = round(min(user_credits / 500 * 100, 100), 1)

    # consistency: active days last 90
    consistency_rows = query(
        """
        SELECT credit_date, SUM(duration) AS total_dur
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id    = ?
          AND  is_deleted = 0
          AND  credit_date >= DATEADD(day, -90, GETDATE())
        GROUP BY credit_date
        """,
        (user_id,),
    )
    active_90 = sum(
        1 for r in consistency_rows
        if (r["total_dur"] or 0) >= settings.streak_min_seconds_per_day
    )
    consistency_score = round(active_90 / 90 * 100, 1)

    # variety: unique categories completed
    variety_rows = query(
        """
        SELECT COUNT(DISTINCT second_level_category_id) AS cats
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        """,
        (user_id,),
    )
    unique_cats = int(variety_rows[0]["cats"] or 0) if variety_rows else 0
    variety_score = round(min(unique_cats, 5) / 5 * 100, 1)

    # recency: user's last-30 credits vs average last-30 across all users
    recency_user_rows = query(
        """
        SELECT SUM(value) AS credits
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id    = ?
          AND  is_deleted = 0
          AND  credit_date >= DATEADD(day, -30, GETDATE())
        """,
        (user_id,),
    )
    user_recency = float(recency_user_rows[0]["credits"] or 0) if recency_user_rows else 0.0

    avg_recency_rows = query(
        """
        SELECT AVG(s.credits) AS avg_credits
        FROM (
            SELECT user_id, SUM(value) AS credits
            FROM   classmate.fact_classmate_learning_credit
            WHERE  is_deleted = 0
              AND  credit_date >= DATEADD(day, -30, GETDATE())
            GROUP BY user_id
        ) s
        """,
    )
    avg_recency = float(avg_recency_rows[0]["avg_credits"] or 1) if avg_recency_rows else 1.0
    if avg_recency == 0:
        avg_recency = 1.0
    recency_score = round(min(user_recency / avg_recency * 50, 100), 1)

    return {
        "current_tier":      current_tier,
        "next_tier":         next_tier,
        "tier_progress":     progress,
        "percentile":        percentile,
        "total_credits":     round(user_credits, 1),
        "credits_score":     credits_score,
        "consistency_score": consistency_score,
        "variety_score":     variety_score,
        "recency_score":     recency_score,
    }


def get_all_user_tiers(conn=None) -> dict:
    """Returns {user_id: tier_str} using absolute credit thresholds (mv table not available)."""
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
