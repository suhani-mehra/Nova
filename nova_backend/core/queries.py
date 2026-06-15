"""
core/queries.py
All parameterised SQL queries for Nova, organised by feature area.
Each function accepts a pyodbc connection as its first argument and
returns a list of dicts via database.query().

Usage:
    from core.database import get_connection
    from core.queries import get_employee_profile
    profile = get_employee_profile(get_connection(), user_id=42)
"""

import pyodbc
from core.database import query


# ══════════════════════════════════════════════════════════════════════════════
# EMPLOYEE QUERIES
# ══════════════════════════════════════════════════════════════════════════════

def get_user_by_adname(conn: pyodbc.Connection, aduser_name: str) -> list[dict]:
    """
    Used by: auth phase — resolve Azure AD login name → dim_user row.
    """
    sql = """
        SELECT id, aduser_name, email_id, first_name, last_name,
               is_active, usertype_id, gender, created_on
        FROM   dim_user
        WHERE  aduser_name = ?
    """
    return query(sql, (aduser_name,))


def get_employee_profile(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """
    Used by: /api/me — full profile for the logged-in employee.
    Joins dim_user + dim_employee_profile on user_id.
    """
    sql = """
        SELECT u.id            AS user_id,
               u.first_name,
               u.last_name,
               u.email_id,
               ep.employee_id,
               ep.display_name,
               ep.department_code,
               ep.designation_code,
               ep.manager,
               ep.office_name,
               ep.country_code,
               ep.is_active
        FROM   dim_user u
        JOIN   dim_employee_profile ep ON ep.user_id = u.id
        WHERE  u.id = ?
          AND  ep.is_deleted = 0
    """
    return query(sql, (user_id,))


def get_user_completed_courses(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """
    Used by: employee dashboard — completed courses list.
    status 4052 = Completed. Duration is in seconds.
    """
    sql = """
        SELECT id, course_name, second_level_category_id,
               learning_credits, start_date, completed_on,
               duration, employee_id, display_name
        FROM   vw_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        ORDER BY completed_on DESC
    """
    return query(sql, (user_id,))


def get_user_inprogress_courses(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """
    Used by: employee dashboard — in-progress courses widget.
    status 4035 = InProgress. Duration is in seconds.
    """
    sql = """
        SELECT id, course_name, second_level_category_id,
               learning_credits, start_date, duration,
               employee_id, display_name
        FROM   vw_trainings
        WHERE  user_id = ?
          AND  status  = 4035
        ORDER BY start_date DESC
    """
    return query(sql, (user_id,))


def get_user_learning_credits_by_date(
    conn: pyodbc.Connection, user_id: int, days: int = 90
) -> list[dict]:
    """
    Used by: employee dashboard — daily learning activity chart / streak calendar.
    Returns total duration (seconds) grouped by credit_date for the last N days.
    credit_date is the streak date field.
    """
    sql = """
        SELECT credit_date,
               SUM(duration) AS total_duration_seconds,
               SUM(value)    AS total_credits
        FROM   fact_learning_credit
        WHERE  user_id    = ?
          AND  is_deleted = 0
          AND  credit_date >= DATEADD(day, -?, GETDATE())
        GROUP BY credit_date
        ORDER BY credit_date DESC
    """
    return query(sql, (user_id, days))


def get_user_quarterly_credits(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """
    Used by: employee dashboard — quarterly progress chart.
    Rows older than 2020 are data-migration artifacts; filter them out.
    """
    sql = """
        SELECT employee_id, user_id, year, quarter, total_credits
        FROM   mv_quarterly_credits
        WHERE  user_id = ?
          AND  year BETWEEN 2020 AND 2026
        ORDER BY year DESC, quarter DESC
    """
    return query(sql, (user_id,))


def get_user_certifications(conn: pyodbc.Connection, user_id: int) -> list[dict]:
    """
    Used by: employee dashboard — certifications panel.
    status 2 = Approved.
    """
    sql = """
        SELECT fc.id, fc.certificate_id, fc.completion_date,
               fc.learning_credits, fc.expiry_date, fc.approved_on,
               dc.certificate_name, dc.certificate_provider,
               dc.learning_credit_value
        FROM   fact_certification fc
        JOIN   dim_certificate dc ON dc.id = fc.certificate_id
        WHERE  fc.user_id    = ?
          AND  fc.status     = 2
          AND  fc.is_deleted = 0
        ORDER BY fc.completion_date DESC
    """
    return query(sql, (user_id,))


# ══════════════════════════════════════════════════════════════════════════════
# MANAGER QUERIES  (scoped to direct reports only via manager = ?)
# ══════════════════════════════════════════════════════════════════════════════

def get_direct_reports(conn: pyodbc.Connection, manager_user_id: int) -> list[dict]:
    """
    Used by: manager dashboard — team roster.
    Only returns employees where dim_employee_profile.manager = manager_user_id.
    """
    sql = """
        SELECT ep.user_id,
               ep.employee_id,
               ep.display_name,
               ep.department_code,
               ep.designation_code,
               ep.office_name,
               u.email_id
        FROM   dim_employee_profile ep
        JOIN   dim_user u ON u.id = ep.user_id
        WHERE  ep.manager    = ?
          AND  ep.is_active  = 1
          AND  ep.is_deleted = 0
        ORDER BY ep.display_name
    """
    return query(sql, (manager_user_id,))


def get_team_course_completions(
    conn: pyodbc.Connection, manager_user_id: int, days: int = 30
) -> list[dict]:
    """
    Used by: manager dashboard — recent team completions feed.
    Returns completed courses (status 4052) for direct reports in the last N days.
    """
    sql = """
        SELECT vt.user_id, vt.course_name, vt.second_level_category_id,
               vt.learning_credits, vt.completed_on, vt.duration,
               vt.employee_id, vt.display_name
        FROM   vw_trainings vt
        JOIN   dim_employee_profile ep ON ep.user_id = vt.user_id
        WHERE  ep.manager     = ?
          AND  ep.is_deleted  = 0
          AND  vt.status      = 4052
          AND  vt.completed_on >= DATEADD(day, -?, GETDATE())
        ORDER BY vt.completed_on DESC
    """
    return query(sql, (manager_user_id, days))


def get_team_quarterly_credits(
    conn: pyodbc.Connection, manager_user_id: int
) -> list[dict]:
    """
    Used by: manager dashboard — team quarterly credits leaderboard / chart.
    Rows older than 2020 are data-migration artifacts.
    """
    sql = """
        SELECT mqc.employee_id, mqc.user_id, mqc.year,
               mqc.quarter, mqc.total_credits
        FROM   mv_quarterly_credits mqc
        WHERE  mqc.user_id IN (
                   SELECT user_id
                   FROM   dim_employee_profile
                   WHERE  manager    = ?
                     AND  is_deleted = 0
               )
          AND  mqc.year BETWEEN 2020 AND 2026
        ORDER BY mqc.year DESC, mqc.quarter DESC, mqc.total_credits DESC
    """
    return query(sql, (manager_user_id,))


def get_team_inprogress(conn: pyodbc.Connection, manager_user_id: int) -> list[dict]:
    """
    Used by: manager dashboard — team in-progress courses overview.
    status 4035 = InProgress.
    """
    sql = """
        SELECT vt.user_id, vt.course_name, vt.second_level_category_id,
               vt.learning_credits, vt.start_date, vt.duration,
               vt.employee_id, vt.display_name
        FROM   vw_trainings vt
        JOIN   dim_employee_profile ep ON ep.user_id = vt.user_id
        WHERE  ep.manager    = ?
          AND  ep.is_deleted = 0
          AND  vt.status     = 4035
        ORDER BY vt.start_date DESC
    """
    return query(sql, (manager_user_id,))


def get_team_certifications(
    conn: pyodbc.Connection, manager_user_id: int
) -> list[dict]:
    """
    Used by: manager dashboard — team certifications panel.
    Uses vw_certification (already joined view).
    """
    sql = """
        SELECT vc.certification_id, vc.user_id, vc.certificate_id,
               vc.completion_date, vc.status, vc.status_name,
               vc.certificate_name, vc.certificate_provider,
               vc.learning_credit_value, vc.expiry_date,
               vc.is_reimnursed, vc.reimbursed_amount,
               vc.first_name, vc.last_name, vc.email_id,
               vc.employee_id
        FROM   vw_certification vc
        WHERE  vc.user_id IN (
                   SELECT user_id
                   FROM   dim_employee_profile
                   WHERE  manager    = ?
                     AND  is_deleted = 0
               )
        ORDER BY vc.completion_date DESC
    """
    return query(sql, (manager_user_id,))


# ══════════════════════════════════════════════════════════════════════════════
# COURSE CATALOGUE QUERIES
# ══════════════════════════════════════════════════════════════════════════════

def get_active_courses(conn: pyodbc.Connection) -> list[dict]:
    """
    Used by: course catalogue page — list all browsable courses.
    Excludes private and inactive courses.
    """
    sql = """
        SELECT id, name, description, learning_credits,
               days_to_complete, level_id, image_name, created_on
        FROM   dim_second_level_cat
        WHERE  is_active  = 1
          AND  is_private = 0
        ORDER BY name
    """
    return query(sql)


def get_course_by_id(conn: pyodbc.Connection, course_id: int) -> list[dict]:
    """
    Used by: course detail page.
    """
    sql = """
        SELECT id, name, description, learning_credits,
               days_to_complete, level_id, image_name, created_on
        FROM   dim_second_level_cat
        WHERE  id = ?
    """
    return query(sql, (course_id,))
