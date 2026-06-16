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


def calculate_tier(user_id: int, conn=None) -> dict:
    all_credits_rows = query(
        """
        SELECT user_id, SUM(total_credits) AS credits
        FROM   classmate.mv_employee_year_quarter_credits
        WHERE  year BETWEEN 2020 AND 2026
        GROUP BY user_id
        """,
    )

    if not all_credits_rows:
        return {
            "current_tier": "starter", "next_tier": "bronze",
            "tier_progress": 0, "percentile": 100.0, "total_credits": 0.0,
            "credits_score": 0.0, "consistency_score": 0.0,
            "variety_score": 0.0, "recency_score": 0.0,
        }

    credit_map = {r["user_id"]: float(r["credits"] or 0) for r in all_credits_rows}
    user_credits = credit_map.get(user_id, 0.0)
    all_values = sorted(credit_map.values())
    n = len(all_values)

    below = sum(1 for v in all_values if v < user_credits)
    percentile = round((1 - below / n) * 100, 1) if n > 1 else 50.0
    current_tier, next_tier = _percentile_to_tier(percentile)
    progress = _tier_progress(percentile)

    max_credits = max(all_values) if all_values else 1
    credits_score = round(user_credits / max_credits * 100, 1) if max_credits else 0.0

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
    """Returns {user_id: tier_str} for every user in the dataset."""
    all_credits_rows = query(
        """
        SELECT user_id, SUM(total_credits) AS credits
        FROM   classmate.mv_employee_year_quarter_credits
        WHERE  year BETWEEN 2020 AND 2026
        GROUP BY user_id
        """,
    )
    if not all_credits_rows:
        return {}

    credit_map = {r["user_id"]: float(r["credits"] or 0) for r in all_credits_rows}
    all_values = sorted(credit_map.values())
    n = len(all_values)

    result = {}
    for uid, credits in credit_map.items():
        below = sum(1 for v in all_values if v < credits)
        percentile = round((1 - below / n) * 100, 1) if n > 1 else 50.0
        tier, _ = _percentile_to_tier(percentile)
        result[uid] = tier
    return result
