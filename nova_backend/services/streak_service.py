"""
services/streak_service.py
Learning streak and weekly activity calculation.
"""

from collections import defaultdict
from datetime import date, timedelta

from core.database import query
from core.config import settings


def calculate_streak(user_id: int, conn=None) -> dict:
    credit_rows = query(
        """
        SELECT credit_date, SUM(duration) AS total_dur
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id    = ?
          AND  is_active  = 1
          AND  is_deleted = 0
        GROUP BY credit_date
        """,
        (user_id,),
    )

    self_study_rows = query(
        """
        SELECT attended_date, SUM(duration) AS total_dur
        FROM   classmate.fact_classmate_self_study
        WHERE  user_id    = ?
          AND  status     = 2
          AND  is_active  = 1
        GROUP BY attended_date
        """,
        (user_id,),
    )

    # merge durations by date
    day_totals: dict[date, int] = defaultdict(int)
    for r in credit_rows:
        if r["credit_date"]:
            d = r["credit_date"].date() if hasattr(r["credit_date"], "date") else r["credit_date"]
            day_totals[d] += int(r["total_dur"] or 0)
    for r in self_study_rows:
        if r["attended_date"]:
            d = r["attended_date"].date() if hasattr(r["attended_date"], "date") else r["attended_date"]
            day_totals[d] += int(r["total_dur"] or 0)

    min_secs = settings.streak_min_seconds_per_day
    active_days = {d for d, dur in day_totals.items() if dur >= min_secs}

    today = date.today()

    # current streak (consecutive days ending today or yesterday)
    streak = 0
    check = today if today in active_days else today - timedelta(days=1)
    while check in active_days:
        streak += 1
        check -= timedelta(days=1)

    # week_map: Mon=0 … Sun=6 for current week
    monday = today - timedelta(days=today.weekday())
    week_map = [(monday + timedelta(days=i)) in active_days for i in range(7)]

    # learning_time this week in seconds
    week_secs = sum(
        day_totals.get(monday + timedelta(days=i), 0)
        for i in range(7)
    )
    hours, remainder = divmod(week_secs, 3600)
    minutes = remainder // 60
    learning_time = f"{hours}h {minutes}m"

    # active days last 30 / 90
    active_30 = sum(
        1 for i in range(30)
        if (today - timedelta(days=i)) in active_days
    )
    active_90 = sum(
        1 for i in range(90)
        if (today - timedelta(days=i)) in active_days
    )

    return {
        "current_streak":    streak,
        "week_map":          week_map,
        "learning_time":     learning_time,
        "active_days_last_30": active_30,
        "active_days_last_90": active_90,
    }
