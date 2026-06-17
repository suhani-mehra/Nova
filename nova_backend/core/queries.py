"""
core/queries.py
All parameterised SQL queries for Nova, organised by feature area.
Each function accepts a pyodbc connection (unused — query() gets its own
cached connection) and returns a list of dicts via database.query().

Usage:
    from core.database import get_connection
    from core.queries import get_employee_profile
    profile = get_employee_profile(get_connection(), user_id=5575)

IMPORTANT: All queries against dim_classmate_employee_profile use the
ROW_NUMBER() dedup CTE to avoid fan-out from SCD versioning (~73k rows
for ~13k active people).  String fields are normalised with LOWER(TRIM())
in SQL and re-capitalised with .title() in Python before being returned.
"""

import pyodbc
from core.database import query

# ── Dedup CTE fragment (reused in every query that touches employee_profile) ──
_DEDUP_CTE = """
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


# ══════════════════════════════════════════════════════════════════════════════
# IDENTITY / PROFILE
# ══════════════════════════════════════════════════════════════════════════════

def get_user_by_id(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """Resolve a numeric dim_classmate_user.id → user row."""
    sql = """
        SELECT id, aduser_name, email_id, first_name, last_name, is_active
        FROM   classmate.dim_classmate_user
        WHERE  id          = ?
          AND  is_active   = 1
          AND  etl_isactive = 1
    """
    return query(sql, (user_id,))


def get_user_by_adname(conn: pyodbc.Connection, aduser_name: str) -> list[dict]:
    """Resolve an Azure AD login name → dim_classmate_user row."""
    sql = """
        SELECT id, aduser_name, email_id, first_name, last_name,
               is_active, usertype_id, gender, created_on
        FROM   classmate.dim_classmate_user
        WHERE  aduser_name  = ?
          AND  is_active    = 1
          AND  etl_isactive = 1
    """
    return query(sql, (aduser_name,))


def get_employee_profile(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """
    Full profile for one employee.  Uses dedup CTE so SCD fan-out is avoided.
    String fields normalised in SQL and .title()'d in Python before return.
    """
    sql = _DEDUP_CTE + """
        SELECT
            u.id                                    AS user_id,
            u.email_id,
            ep.employee_id,
            LOWER(TRIM(ep.display_name))            AS name,
            LOWER(TRIM(ep.department_code))         AS department,
            LOWER(TRIM(ep.designation_code))        AS designation,
            ep.manager                              AS manager_user_id,
            ep.office_name,
            ep.country_code
        FROM   latest_profiles ep
        JOIN   classmate.dim_classmate_user u ON u.id = ep.user_id
        WHERE  ep.rn      = 1
          AND  ep.user_id = ?
    """
    rows = query(sql, (user_id,))
    for r in rows:
        if r.get("name"):
            r["name"] = r["name"].title()
        if r.get("department"):
            r["department"] = r["department"].title()
        if r.get("designation"):
            r["designation"] = r["designation"].title()
    return rows


def is_manager(conn: pyodbc.Connection, user_id: int) -> bool:
    """Return True if user_id appears as manager for at least one active employee."""
    sql = _DEDUP_CTE + """
        SELECT COUNT(*) AS cnt
        FROM   latest_profiles
        WHERE  rn      = 1
          AND  manager = ?
    """
    rows = query(sql, (user_id,))
    return bool(rows and int(rows[0]["cnt"] or 0) > 0)


def get_direct_reports(conn: pyodbc.Connection, manager_user_id: int) -> list[dict]:
    """
    All active direct reports for manager_user_id.
    Uses dedup CTE.  Strings normalised and .title()'d before return.
    """
    sql = _DEDUP_CTE + """
        SELECT
            ep.user_id,
            u.email_id,
            ep.employee_id,
            LOWER(TRIM(ep.display_name))    AS name,
            LOWER(TRIM(ep.department_code)) AS department,
            LOWER(TRIM(ep.designation_code)) AS designation
        FROM   latest_profiles ep
        JOIN   classmate.dim_classmate_user u ON u.id = ep.user_id
        WHERE  ep.rn      = 1
          AND  ep.manager = ?
        ORDER BY ep.display_name
    """
    rows = query(sql, (manager_user_id,))
    for r in rows:
        if r.get("name"):
            r["name"] = r["name"].title()
        if r.get("department"):
            r["department"] = r["department"].title()
        if r.get("designation"):
            r["designation"] = r["designation"].title()
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# EMPLOYEE ACTIVITY QUERIES  (no employee_profile join needed)
# ══════════════════════════════════════════════════════════════════════════════

def get_user_completed_courses(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """Completed courses for one employee. status 4052 = Completed."""
    sql = """
        SELECT id, course_name, second_level_category_id,
               learning_credits, start_date, completed_on,
               duration, employee_id, display_name
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        ORDER BY completed_on DESC
    """
    return query(sql, (user_id,))


def get_user_inprogress_courses(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """In-progress courses for one employee. status 4035 = InProgress."""
    sql = """
        SELECT id, course_name, second_level_category_id,
               learning_credits, start_date, duration,
               employee_id, display_name
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4035
        ORDER BY start_date DESC
    """
    return query(sql, (user_id,))


def get_user_learning_credits_by_date(
    conn: pyodbc.Connection, user_id: int, days: int = 90
) -> list[dict]:
    """Daily learning activity for streak calendar. credit_date is the streak field."""
    sql = """
        SELECT credit_date,
               SUM(duration) AS total_duration_seconds,
               SUM(value)    AS total_credits
        FROM   classmate.fact_classmate_learning_credit
        WHERE  user_id    = ?
          AND  is_deleted = 0
          AND  credit_date >= DATEADD(day, -?, GETDATE())
        GROUP BY credit_date
        ORDER BY credit_date DESC
    """
    return query(sql, (user_id, days))


def get_user_quarterly_credits(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """Quarterly credits from the materialized view. Rows before 2020 are artifacts."""
    sql = """
        SELECT employee_id, user_id, year, quarter, total_credits
        FROM   classmate.mv_employee_year_quarter_credits
        WHERE  user_id = ?
          AND  year BETWEEN 2020 AND 2026
        ORDER BY year DESC, quarter DESC
    """
    return query(sql, (user_id,))


def get_user_certifications(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """Approved certifications for one employee via the certification view."""
    sql = """
        SELECT certification_id, user_id, certificate_id,
               completion_date, status, status_name,
               certificate_name, certificate_provider,
               learning_credit_value, expiry_date,
               first_name, last_name, email_id, employee_id
        FROM   classmate.vw_classmate_certification
        WHERE  user_id = ?
          AND  status  = 2
        ORDER BY completion_date DESC
    """
    return query(sql, (user_id,))


# ══════════════════════════════════════════════════════════════════════════════
# MANAGER QUERIES  (scoped to direct reports via manager = ?)
# ══════════════════════════════════════════════════════════════════════════════

def get_manager_monthly_trend(
    conn: pyodbc.Connection, manager_user_id: int
) -> list[dict]:
    """
    Last 6 months of completions for a manager's direct reports.
    Uses dedup CTE on employee_profile join.
    """
    sql = _DEDUP_CTE + """
        SELECT
            FORMAT(vt.completed_on, 'MMM') AS month,
            MONTH(vt.completed_on)          AS month_num,
            YEAR(vt.completed_on)           AS year_num,
            SUM(vt.learning_credits)        AS credits,
            COUNT(*)                        AS completions
        FROM   classmate.vw_classmate_trainings vt
        JOIN   latest_profiles ep ON ep.user_id = vt.user_id
        WHERE  ep.rn       = 1
          AND  ep.manager  = ?
          AND  vt.status   = 4052
          AND  vt.completed_on >= DATEADD(month, -6, GETDATE())
        GROUP BY FORMAT(vt.completed_on, 'MMM'),
                 MONTH(vt.completed_on),
                 YEAR(vt.completed_on)
        ORDER BY year_num, month_num
    """
    rows = query(sql, (manager_user_id,))
    return [
        {
            "month":       r["month"],
            "credits":     round(float(r["credits"] or 0), 1),
            "completions": r["completions"],
        }
        for r in rows
    ]


def get_team_course_completions(
    conn: pyodbc.Connection, manager_user_id: int, days: int = 30
) -> list[dict]:
    """Recent completions (status 4052) for a manager's direct reports."""
    sql = _DEDUP_CTE + """
        SELECT vt.user_id, vt.course_name, vt.second_level_category_id,
               vt.learning_credits, vt.completed_on, vt.duration,
               vt.employee_id, vt.display_name
        FROM   classmate.vw_classmate_trainings vt
        JOIN   latest_profiles ep ON ep.user_id = vt.user_id
        WHERE  ep.rn           = 1
          AND  ep.manager      = ?
          AND  vt.status       = 4052
          AND  vt.completed_on >= DATEADD(day, -?, GETDATE())
        ORDER BY vt.completed_on DESC
    """
    return query(sql, (manager_user_id, days))


def get_team_quarterly_credits(
    conn: pyodbc.Connection, manager_user_id: int
) -> list[dict]:
    """Quarterly credits for a manager's direct reports."""
    sql = _DEDUP_CTE + """
        SELECT mqc.employee_id, mqc.user_id, mqc.year,
               mqc.quarter, mqc.total_credits
        FROM   classmate.mv_employee_year_quarter_credits mqc
        WHERE  mqc.user_id IN (
                   SELECT user_id FROM latest_profiles
                   WHERE  rn = 1 AND manager = ?
               )
          AND  mqc.year BETWEEN 2020 AND 2026
        ORDER BY mqc.year DESC, mqc.quarter DESC, mqc.total_credits DESC
    """
    return query(sql, (manager_user_id,))


def get_team_inprogress(conn: pyodbc.Connection, manager_user_id: int) -> list[dict]:
    """In-progress courses (status 4035) for a manager's direct reports."""
    sql = _DEDUP_CTE + """
        SELECT vt.user_id, vt.course_name, vt.second_level_category_id,
               vt.learning_credits, vt.start_date, vt.duration,
               vt.employee_id, vt.display_name
        FROM   classmate.vw_classmate_trainings vt
        JOIN   latest_profiles ep ON ep.user_id = vt.user_id
        WHERE  ep.rn      = 1
          AND  ep.manager = ?
          AND  vt.status  = 4035
        ORDER BY vt.start_date DESC
    """
    return query(sql, (manager_user_id,))


def get_team_certifications(
    conn: pyodbc.Connection, manager_user_id: int
) -> list[dict]:
    """Approved certifications for a manager's direct reports."""
    sql = _DEDUP_CTE + """
        SELECT vc.certification_id, vc.user_id, vc.certificate_id,
               vc.completion_date, vc.status, vc.status_name,
               vc.certificate_name, vc.certificate_provider,
               vc.learning_credit_value, vc.expiry_date,
               vc.first_name, vc.last_name, vc.email_id,
               vc.employee_id
        FROM   classmate.vw_classmate_certification vc
        WHERE  vc.user_id IN (
                   SELECT user_id FROM latest_profiles
                   WHERE  rn = 1 AND manager = ?
               )
        ORDER BY vc.completion_date DESC
    """
    return query(sql, (manager_user_id,))


# ══════════════════════════════════════════════════════════════════════════════
# COURSE CATALOGUE
# ══════════════════════════════════════════════════════════════════════════════

def get_active_courses(conn: pyodbc.Connection) -> list[dict]:
    """All browsable courses — excludes private and inactive."""
    sql = """
        SELECT id, name, description, learning_credits,
               days_to_complete, level_id, image_name, created_on
        FROM   classmate.dim_classmate_second_level_category
        WHERE  is_active  = 1
          AND  is_private = 0
        ORDER BY name
    """
    return query(sql)


def get_course_by_id(conn: pyodbc.Connection, course_id: int) -> list[dict]:
    sql = """
        SELECT id, name, description, learning_credits,
               days_to_complete, level_id, image_name, created_on
        FROM   classmate.dim_classmate_second_level_category
        WHERE  id = ?
    """
    return query(sql, (course_id,))
