"""
core/database.py
Query layer over the local SQLite warehouse (nova_warehouse.db).

The warehouse is a synced copy of the Classmate lakehouse tables, refreshed
from the table-dump API by nova_db.warehouse_sync (nightly + on demand).
Replaces the old direct pyodbc connection to Microsoft Fabric.

Each query opens a short-lived READ-ONLY connection: sub-millisecond for a
local file, inherently thread-safe, and it means an in-flight nightly sync
(which atomically swaps the DB file) can never corrupt a reader.

Usage (unchanged from the Fabric era):
    from core.database import get_connection, query, query_df
"""

import logging
import re
import sqlite3
from datetime import date, datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# pyodbc returned DATE/DATETIME columns as datetime.date/datetime.datetime
# objects; SQLite stores them as ISO strings. Convert strict ISO strings back
# so downstream Python date arithmetic keeps working unchanged.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _revive(value):
    """ISO date/datetime string → date/datetime object (pyodbc parity)."""
    if isinstance(value, str):
        if _DATETIME_RE.match(value):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value
        if _DATE_RE.match(value):
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                return value
    return value


def _adapt_params(params: Optional[tuple]) -> tuple:
    """date/datetime params → ISO strings (matches the stored 'T' format)."""
    if not params:
        return ()
    return tuple(
        p.isoformat() if isinstance(p, (datetime, date)) else p
        for p in params
    )


def _warehouse_path():
    from nova_db.warehouse_sync import warehouse_path
    return warehouse_path()


def get_connection() -> sqlite3.Connection:
    """
    Read-only connection to the warehouse. Kept for legacy callers that pass
    a `conn` around (core/queries.py functions accept-and-ignore it).
    """
    path = _warehouse_path()
    if not path.exists():
        raise RuntimeError(
            f"Warehouse DB not found at {path}. "
            "Run the sync first: python -m nova_db.warehouse_sync "
            "(or POST /api/admin/sync)."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return conn


def query(sql: str, params: Optional[tuple] = None) -> list[dict]:
    """Executes a parameterised SQL query and returns results as a list of dicts."""
    conn = get_connection()
    try:
        cursor = conn.execute(sql, _adapt_params(params))
        columns = [col[0] for col in cursor.description]
        return [
            {col: _revive(val) for col, val in zip(columns, row)}
            for row in cursor.fetchall()
        ]
    except Exception as exc:
        logger.error("Query failed: %s\nSQL: %s\nParams: %s", exc, sql, params)
        raise
    finally:
        conn.close()


def query_df(sql: str, params: Optional[tuple] = None) -> pd.DataFrame:
    """Executes a parameterised SQL query and returns results as a pandas DataFrame."""
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=_adapt_params(params))
    except Exception as exc:
        logger.error("query_df failed: %s\nSQL: %s\nParams: %s", exc, sql, params)
        raise
    finally:
        conn.close()
