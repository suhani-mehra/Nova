"""
nova_db/warehouse_sync.py
Sync the Classmate table-dump API into a local SQLite warehouse.

The API only returns whole tables (no filtering/joins/aggregation), so we pull
all 10 tables into nova_warehouse.db on a schedule and the app queries SQLite.

Strategy:
  - build everything into a staging file (<db>.new)
  - create indexes + the two derived objects the app needs but the API lacks
    (mv_employee_year_quarter_credits, vw_classmate_certification)
  - os.replace() the staging file over the live one → readers always see a
    complete, immutable snapshot (no WAL needed; the file is swapped atomically)

Usage:
    from nova_db.warehouse_sync import sync_all, warehouse_is_ready
    sync_all()
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.api_client import fetch_table

logger = logging.getLogger(__name__)

# All tables exposed by the API, in sync order (dims first, then facts).
API_TABLES = [
    "dim_classmate_user",
    "dim_classmate_employee_profile",
    "dim_classmate_second_level_category",
    "dim_classmate_content_mapping",
    "dim_classmate_certificate",
    "vw_classmate_trainings",
    "fact_classmate_learning_credit",
    "fact_classmate_user_skill_status",
    "fact_classmate_self_study",
    "fact_classmate_certification",
    "employee_role",
]

# Indexes on the join/filter columns the app's queries actually use.
_INDEXES = [
    ("dim_classmate_user", "id"),
    ("dim_classmate_user", "aduser_name"),
    ("dim_classmate_employee_profile", "user_id"),
    ("dim_classmate_employee_profile", "manager"),
    ("vw_classmate_trainings", "user_id"),
    ("vw_classmate_trainings", "completed_on"),
    ("fact_classmate_learning_credit", "user_id"),
    ("fact_classmate_learning_credit", "credit_date"),
    ("fact_classmate_user_skill_status", "user_id"),
    ("fact_classmate_self_study", "user_id"),
    ("fact_classmate_certification", "user_id"),
    ("dim_classmate_certificate", "id"),
    ("dim_classmate_second_level_category", "id"),
    ("mv_employee_year_quarter_credits", "user_id"),
    ("vw_classmate_certification", "user_id"),
    ("employee_role", "employee_id"),
]

def warehouse_path() -> Path:
    """Absolute path of the live warehouse DB (relative paths → nova_backend/)."""
    from core.config import settings
    p = Path(settings.warehouse_db_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def warehouse_is_ready() -> bool:
    """True if the warehouse exists and contains a completed sync."""
    path = warehouse_path()
    if not path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM _sync_meta WHERE row_count > 0"
            ).fetchone()
            return bool(row and row[0] >= len(API_TABLES) - 2)  # tolerate empty edge tables
    except sqlite3.Error:
        return False


def last_sync_time() -> str | None:
    path = warehouse_path()
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT MAX(synced_at) FROM _sync_meta").fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def _qident(name: str) -> str:
    """Quote an SQL identifier (handles oddities like the `comments'` column)."""
    return '"' + name.replace('"', '""') + '"'


def _load_table(conn: sqlite3.Connection, table_name: str) -> int:
    """Stream one API table into SQLite; returns rows inserted.

    table_name always comes from the hardcoded API_TABLES list (never request
    input); column names come from the API response and are quoted via
    _qident() before being interpolated into DDL/DML, so the f-strings below
    are safe by construction, not string-built from untrusted values.
    """
    total = 0
    columns: list[str] | None = None
    for page in fetch_table(table_name):
        if not page:
            continue
        if columns is None:
            columns = list(page[0].keys())
            col_defs = ", ".join(_qident(c) for c in columns)
            conn.execute(f"DROP TABLE IF EXISTS {_qident(table_name)}")
            conn.execute(f"CREATE TABLE {_qident(table_name)} ({col_defs})")
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {_qident(table_name)} VALUES ({placeholders})"
        conn.executemany(sql, [tuple(row.get(c) for c in columns) for row in page])
        total += len(page)
    if columns is None:
        # Empty table — create it with no rows so queries don't crash.
        logger.warning("API table %s returned no rows", table_name)
        conn.execute(f"DROP TABLE IF EXISTS {_qident(table_name)}")
        conn.execute(f"CREATE TABLE {_qident(table_name)} (id)")
    conn.commit()
    return total


def _build_derived(conn: sqlite3.Connection) -> None:
    """Rebuild the two objects the app queries but the API doesn't expose."""

    # 1. mv_employee_year_quarter_credits — quarterly credit totals per user.
    conn.execute("DROP TABLE IF EXISTS mv_employee_year_quarter_credits")
    conn.execute("""
        CREATE TABLE mv_employee_year_quarter_credits AS
        WITH latest_profiles AS (
            SELECT user_id, employee_id,
                   ROW_NUMBER() OVER (
                     PARTITION BY user_id
                     ORDER BY modified_on DESC
                   ) AS rn
            FROM dim_classmate_employee_profile
            WHERE etl_isactive = 1
              AND is_active    = 1
              AND is_deleted   = 0
              AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
              AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
        )
        SELECT lp.employee_id                                              AS employee_id,
               f.user_id                                                   AS user_id,
               CAST(strftime('%Y', f.credit_date) AS INTEGER)              AS year,
               (CAST(strftime('%m', f.credit_date) AS INTEGER) + 2) / 3    AS quarter,
               SUM(f.value)                                                AS total_credits
        FROM   fact_classmate_learning_credit f
        LEFT JOIN latest_profiles lp
               ON lp.user_id = f.user_id AND lp.rn = 1
        WHERE  f.is_deleted = 0
          AND  f.credit_date IS NOT NULL
        GROUP BY f.user_id, year, quarter
    """)

    # 2. vw_classmate_certification — certification fact enriched with
    #    certificate, user and profile columns (mirrors the old Fabric view).
    #    status codes: 1=Pending, 2=Approved, 3=Rejected (app filters status=2).
    conn.execute("DROP TABLE IF EXISTS vw_classmate_certification")
    conn.execute("""
        CREATE TABLE vw_classmate_certification AS
        WITH latest_profiles AS (
            SELECT user_id, employee_id,
                   ROW_NUMBER() OVER (
                     PARTITION BY user_id
                     ORDER BY modified_on DESC
                   ) AS rn
            FROM dim_classmate_employee_profile
            WHERE etl_isactive = 1
              AND is_active    = 1
              AND is_deleted   = 0
              AND (employee_id IS NULL OR UPPER(TRIM(employee_id)) NOT LIKE 'TMP%')
              AND country_code IS NOT NULL AND UPPER(TRIM(country_code)) != 'OT'
        )
        SELECT f.id                     AS certification_id,
               f.user_id                AS user_id,
               f.certificate_id         AS certificate_id,
               f.completion_date        AS completion_date,
               f.status                 AS status,
               CASE f.status
                    WHEN 1 THEN 'Pending'
                    WHEN 2 THEN 'Approved'
                    WHEN 3 THEN 'Rejected'
                    ELSE 'Unknown'
               END                      AS status_name,
               c.certificate_name       AS certificate_name,
               c.certificate_provider   AS certificate_provider,
               c.learning_credit_value  AS learning_credit_value,
               f.expiry_date            AS expiry_date,
               u.first_name             AS first_name,
               u.last_name              AS last_name,
               u.email_id               AS email_id,
               lp.employee_id           AS employee_id
        FROM   fact_classmate_certification f
        LEFT JOIN dim_classmate_certificate c ON c.id = f.certificate_id
        LEFT JOIN dim_classmate_user u        ON u.id = f.user_id
        LEFT JOIN latest_profiles lp
               ON lp.user_id = f.user_id AND lp.rn = 1
        WHERE  f.is_deleted = 0
    """)
    conn.commit()


def _create_indexes(conn: sqlite3.Connection) -> None:
    for table, col in _INDEXES:
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {_qident(f'idx_{table}_{col}')} "
            f"ON {_qident(table)} ({_qident(col)})"
        )
    conn.commit()


def sync_all() -> dict:
    """
    Full refresh of the warehouse from the API into a staging file, then an
    atomic swap over the live DB. Returns {table: row_count}.
    """
    live_path = warehouse_path()
    staging_path = live_path.with_suffix(live_path.suffix + ".new")
    if staging_path.exists():
        staging_path.unlink()

    logger.info("Warehouse sync starting → %s", staging_path)
    counts: dict[str, int] = {}
    conn = sqlite3.connect(staging_path)
    try:
        conn.execute("PRAGMA synchronous = OFF")   # staging file — speed over durability
        for table in API_TABLES:
            counts[table] = _load_table(conn, table)
            logger.info("Synced %s: %d rows", table, counts[table])

        _build_derived(conn)
        counts["mv_employee_year_quarter_credits"] = conn.execute(
            "SELECT COUNT(*) FROM mv_employee_year_quarter_credits").fetchone()[0]
        counts["vw_classmate_certification"] = conn.execute(
            "SELECT COUNT(*) FROM vw_classmate_certification").fetchone()[0]

        _create_indexes(conn)

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("CREATE TABLE _sync_meta (name TEXT PRIMARY KEY, row_count INTEGER, synced_at TEXT)")
        conn.executemany(
            "INSERT INTO _sync_meta VALUES (?, ?, ?)",
            [(name, n, now) for name, n in counts.items()],
        )
        conn.commit()
    finally:
        conn.close()

    os.replace(staging_path, live_path)
    logger.info("Warehouse sync complete: %s", counts)
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sync_all()
