"""
services/team_service.py
Team-level highlights, accomplishments, and at-risk detection.
"""

from datetime import date, timedelta

from core.database import query
from services.skill_service import _classify
from services.streak_service import calculate_streak


def get_team_highlights(manager_user_id: int, conn=None) -> dict:
    reports = query(
        """
        SELECT user_id, display_name
        FROM   dim_classmate_employee_profile
        WHERE  manager      = ?
          AND  is_active    = 1
          AND  is_deleted   = 0
          AND  etl_isactive = 1
          AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          AND  country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
        """,
        (manager_user_id,),
    )
    if not reports:
        return {
            "top_learner":     {"name": "N/A", "credits": 0},
            "most_improved":   {"name": "N/A", "delta": 0},
            "streak_leader":   {"name": "N/A", "streak": 0},
        }

    uid_name = {r["user_id"]: r["display_name"] for r in reports}
    uids = list(uid_name.keys())
    placeholders = ",".join("?" * len(uids))

    # credits this month per user
    this_month_rows = query(
        f"""
        SELECT user_id, SUM(value) AS credits
        FROM   fact_classmate_learning_credit
        WHERE  user_id IN ({placeholders})
          AND  is_deleted = 0
          AND  credit_date >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
        GROUP BY user_id
        """,
        tuple(uids),
    )
    this_month = {r["user_id"]: float(r["credits"] or 0) for r in this_month_rows}

    # credits last month per user
    last_month_rows = query(
        f"""
        SELECT user_id, SUM(value) AS credits
        FROM   fact_classmate_learning_credit
        WHERE  user_id IN ({placeholders})
          AND  is_deleted = 0
          AND  credit_date >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-60 days')
          AND  credit_date <  strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
        GROUP BY user_id
        """,
        tuple(uids),
    )
    last_month = {r["user_id"]: float(r["credits"] or 0) for r in last_month_rows}

    top_uid = max(uids, key=lambda uid: this_month.get(uid, 0))
    improved_uid = max(
        uids,
        key=lambda uid: this_month.get(uid, 0) - last_month.get(uid, 0),
    )

    # streak leader — calculate for each team member
    best_streak_uid = None
    best_streak = -1
    for uid in uids:
        try:
            s = calculate_streak(uid)
            if s["current_streak"] > best_streak:
                best_streak = s["current_streak"]
                best_streak_uid = uid
        except Exception:
            pass

    return {
        "top_learner": {
            "name":    uid_name.get(top_uid, ""),
            "credits": round(this_month.get(top_uid, 0), 1),
        },
        "most_improved": {
            "name":  uid_name.get(improved_uid, ""),
            "delta": round(
                this_month.get(improved_uid, 0) - last_month.get(improved_uid, 0), 1
            ),
        },
        "streak_leader": {
            "name":   uid_name.get(best_streak_uid, "N/A") if best_streak_uid else "N/A",
            "streak": best_streak if best_streak >= 0 else 0,
        },
    }


def get_team_accomplishments(
    manager_user_id: int, days: int = 30, own_user_id: int = None, conn=None
) -> list:
    """
    Completions for everyone who reports to manager_user_id (peers),
    plus everyone who reports directly to own_user_id (direct reports),
    when own_user_id is supplied.
    """
    if own_user_id is not None:
        # Include both peer group and own direct reports
        rows = query(
            """
            SELECT vt.user_id, vt.display_name AS employee_name,
                vt.course_name, vt.completed_on, vt.learning_credits
            FROM   vw_classmate_trainings vt
            WHERE  vt.status       = 4052
              AND  vt.completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', printf('-%d days', ?))
              AND  vt.user_id     != ?
              AND  vt.user_id IN (
                  SELECT DISTINCT user_id
                  FROM   dim_classmate_employee_profile
                  WHERE  manager IN (?, ?)
                    AND  is_active    = 1
                    AND  is_deleted   = 0
                    AND  etl_isactive = 1
                    AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
                    AND  country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
              )
            ORDER BY vt.completed_on DESC
        LIMIT 50
        """,
            (days, own_user_id, manager_user_id, own_user_id),
        )
    else:
        rows = query(
            """
            SELECT vt.user_id, vt.display_name AS employee_name,
                vt.course_name, vt.completed_on, vt.learning_credits
            FROM   vw_classmate_trainings vt
            WHERE  vt.status       = 4052
              AND  vt.completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', printf('-%d days', ?))
              AND  vt.user_id IN (
                  SELECT DISTINCT user_id
                  FROM   dim_classmate_employee_profile
                  WHERE  manager      = ?
                    AND  is_active    = 1
                    AND  is_deleted   = 0
                    AND  etl_isactive = 1
                    AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
                    AND  country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
              )
            ORDER BY vt.completed_on DESC
        LIMIT 50
        """,
            (days, manager_user_id),
        )
    return [
        {
            "user_id":         r["user_id"],
            "employee_name":   r["employee_name"],
            "course_name":     r["course_name"],
            "completed_on":    str(r["completed_on"])[:10] if r["completed_on"] else None,
            "learning_credits": float(r["learning_credits"] or 0),
            "category":        _classify(r["course_name"] or "") or "Other",
        }
        for r in rows
    ]


def get_team_course_popularity(manager_user_id: int, conn=None) -> list:
    rows = query(
        """
        SELECT vt.course_name, COUNT(*) AS completion_count
        FROM   vw_classmate_trainings vt
        WHERE  vt.status = 4052
          AND  vt.user_id IN (
              SELECT DISTINCT user_id
              FROM   dim_classmate_employee_profile
              WHERE  manager      = ?
                AND  is_active    = 1
                AND  is_deleted   = 0
                AND  etl_isactive = 1
                AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
                AND  country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
          )
        GROUP BY vt.course_name
        ORDER BY completion_count DESC
        LIMIT 5
        """,
        (manager_user_id,),
    )
    return [
        {
            "course_name":      r["course_name"],
            "completion_count": r["completion_count"],
            "category":         _classify(r["course_name"] or "") or "Other",
        }
        for r in rows
    ]


def get_at_risk_employees(manager_user_id: int, conn=None) -> list:
    reports = query(
        """
        SELECT user_id, display_name
        FROM   dim_classmate_employee_profile
        WHERE  manager      = ?
          AND  is_active    = 1
          AND  is_deleted   = 0
          AND  etl_isactive = 1
          AND  (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
          AND  country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
        """,
        (manager_user_id,),
    )
    if not reports:
        return []

    uid_name = {r["user_id"]: r["display_name"] for r in reports}
    uids = list(uid_name.keys())
    placeholders = ",".join("?" * len(uids))

    # last activity date per user
    last_active_rows = query(
        f"""
        SELECT user_id, MAX(credit_date) AS last_date
        FROM   fact_classmate_learning_credit
        WHERE  user_id IN ({placeholders})
          AND  is_deleted = 0
        GROUP BY user_id
        """,
        tuple(uids),
    )
    last_active_map = {r["user_id"]: r["last_date"] for r in last_active_rows}

    # credits this quarter per user (via trainings view — mv table not available in Fabric)
    quarter_rows = query(
        f"""
        SELECT user_id, SUM(learning_credits) AS credits
        FROM   vw_classmate_trainings
        WHERE  user_id IN ({placeholders})
          AND  status = 4052
          AND  completed_on >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-90 days')
        GROUP BY user_id
        """,
        tuple(uids),
    )
    quarter_credits = {r["user_id"]: float(r["credits"] or 0) for r in quarter_rows}

    avg_credits = (
        sum(quarter_credits.values()) / len(quarter_credits)
        if quarter_credits else 0.0
    )

    today = date.today()
    result = []
    for uid in uids:
        last = last_active_map.get(uid)
        if last:
            last_date = last.date() if hasattr(last, "date") else last
            days_inactive = (today - last_date).days
        else:
            days_inactive = 999

        q_credits = quarter_credits.get(uid, 0.0)

        if days_inactive >= 14 and q_credits < avg_credits:
            result.append({
                "employee_name":       uid_name[uid],
                "days_inactive":       days_inactive,
                "credits_this_quarter": round(q_credits, 1),
                "vs_team_average":     round(q_credits - avg_credits, 1),
            })

    return sorted(result, key=lambda x: x["days_inactive"], reverse=True)
