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
        FROM   classmate.dim_classmate_employee_profile
        WHERE  manager    = ?
          AND  is_active  = 1
          AND  is_deleted = 0
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
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id IN ({placeholders})
          AND  is_deleted = 0
          AND  credit_date >= DATEADD(day, -30, GETDATE())
        GROUP BY user_id
        """,
        tuple(uids),
    )
    this_month = {r["user_id"]: float(r["credits"] or 0) for r in this_month_rows}

    # credits last month per user
    last_month_rows = query(
        f"""
        SELECT user_id, SUM(value) AS credits
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id IN ({placeholders})
          AND  is_deleted = 0
          AND  credit_date >= DATEADD(day, -60, GETDATE())
          AND  credit_date <  DATEADD(day, -30, GETDATE())
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


def get_team_accomplishments(manager_user_id: int, conn=None, days: int = 7) -> list:
    rows = query(
        """
        SELECT vt.user_id, vt.display_name AS employee_name,
               vt.course_name, vt.completed_on, vt.learning_credits
        FROM   classmate.vw_classmate_trainings vt
        JOIN   classmate.dim_classmate_employee_profile ep ON ep.user_id = vt.user_id
        WHERE  ep.manager    = ?
          AND  ep.is_deleted = 0
          AND  vt.status     = 4052
          AND  vt.completed_on >= DATEADD(day, -?, GETDATE())
        ORDER BY vt.completed_on DESC
        """,
        (manager_user_id, days),
    )
    return [
        {
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
        SELECT TOP 5 vt.course_name, COUNT(*) AS completion_count
        FROM   classmate.vw_classmate_trainings vt
        JOIN   classmate.dim_classmate_employee_profile ep ON ep.user_id = vt.user_id
        WHERE  ep.manager    = ?
          AND  ep.is_deleted = 0
          AND  vt.status     = 4052
        GROUP BY vt.course_name
        ORDER BY completion_count DESC
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
        FROM   classmate.dim_classmate_employee_profile
        WHERE  manager    = ?
          AND  is_active  = 1
          AND  is_deleted = 0
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
        FROM   classmate.fact_classmate_learning_credit
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
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id IN ({placeholders})
          AND  status = 4052
          AND  completed_on >= DATEADD(day, -90, GETDATE())
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
