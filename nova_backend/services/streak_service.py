"""
services/streak_service.py
Learning streak and weekly activity calculation.
"""

from datetime import date, timedelta

from core.database import query


def calculate_streak(user_id: int, conn=None) -> dict:
    # Three-source union: any of these means the user was active on Classmate that day.
    # No duration threshold — the mere act of interacting with content is the signal.
    activity_rows = query(
        """
        SELECT DISTINCT CAST(activity_date AS DATE) AS activity_date
        FROM (
            -- Source 1: learning credits with actual duration (live sessions, recorded trainings)
            SELECT credit_date AS activity_date
            FROM classmate.fact_classmate_learning_credit
            WHERE user_id    = ?
              AND is_deleted = 0
              AND duration   > 0

            UNION

            -- Source 2: any course interaction (open, progress save, or completion)
            SELECT CAST(modified_on AS DATE)
            FROM classmate.fact_classmate_user_skill_status
            WHERE user_id    = ?
              AND is_deleted = 0
              AND is_active  = 1

            UNION

            -- Source 3: approved self-study sessions
            SELECT attended_date
            FROM classmate.fact_classmate_self_study
            WHERE user_id    = ?
              AND status     = 2
              AND is_deleted = 0
        ) src
        WHERE activity_date IS NOT NULL
        """,
        (user_id, user_id, user_id),
    )

    active_days = set()
    for r in activity_rows:
        d = r["activity_date"]
        if d:
            active_days.add(d.date() if hasattr(d, "date") else d)

    today = date.today()

    # Current streak: consecutive active days ending today or yesterday
    streak = 0
    check = today if today in active_days else today - timedelta(days=1)
    while check in active_days:
        streak += 1
        check -= timedelta(days=1)

    # week_map: Mon=0 … Sun=6 for the current week
    monday = today - timedelta(days=today.weekday())
    week_map = [(monday + timedelta(days=i)) in active_days for i in range(7)]

    # Learning time this week — read from learning_credit for actual duration
    week_rows = query(
        """
        SELECT SUM(duration) AS total_dur
        FROM classmate.fact_classmate_learning_credit
        WHERE user_id    = ?
          AND is_deleted = 0
          AND duration   > 0
          AND credit_date >= ?
          AND credit_date <= ?
        """,
        (user_id, monday, monday + timedelta(days=6)),
    )
    week_secs = int((week_rows[0]["total_dur"] or 0) if week_rows else 0)
    hours, remainder = divmod(week_secs, 3600)
    minutes = remainder // 60
    learning_time = f"{hours}h {minutes}m"

    # Active days last 30 / 90 (used by tier_service for consistency score)
    active_30 = sum(1 for i in range(30) if (today - timedelta(days=i)) in active_days)
    active_90 = sum(1 for i in range(90) if (today - timedelta(days=i)) in active_days)

    return {
        "current_streak":      streak,
        "week_map":            week_map,
        "learning_time":       learning_time,
        "active_days_last_30": active_30,
        "active_days_last_90": active_90,
    }
