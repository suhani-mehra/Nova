"""
nova_db/admin_overrides.py
SQLite-backed admin overrides layered on top of the synced Classmate warehouse.

Manager allocation and exec status are otherwise derived live from Classmate on
every request. These two tables are the only persistent override layer in Nova,
and they live in nova_local.db — NOT the warehouse, which warehouse_sync.sync_all()
atomically replaces — so admin-set overrides survive every re-sync:

  admin_manager_overrides  — reassign an employee to a different manager
  admin_exec_overrides     — grant/revoke exec-manager status

Every read path consults these first and falls back to the Classmate-derived
value: core.queries.is_manager / get_direct_reports (manager allocation) and
routers.manager._is_exec_manager (exec status).

Writes happen only via the admin API (routers/admin.py), gated on ADMIN_USER_IDS.
Mirrors the structure of nova_db/user_settings.py.
"""

import logging
import sqlite3
from pathlib import Path

from core.config import settings

logger = logging.getLogger(__name__)

_DB_PATH = Path(settings.nova_local_db_path)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_admin_overrides_tables() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_manager_overrides (
                user_id         INTEGER PRIMARY KEY,
                manager_user_id INTEGER NOT NULL,
                set_by_user_id  INTEGER,
                set_at          TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_exec_overrides (
                user_id        INTEGER PRIMARY KEY,
                is_exec        INTEGER NOT NULL,
                set_by_user_id INTEGER,
                set_at         TEXT
            )
            """
        )
        c.commit()


# ── Manager overrides ─────────────────────────────────────────────────────────

def get_manager_overrides() -> dict:
    """Return {employee_user_id: manager_user_id} for all manager overrides.
    Degrades to {} (pure Classmate behavior) if the table is missing/unreadable —
    this is called on every request, so it must never raise."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT user_id, manager_user_id FROM admin_manager_overrides"
            ).fetchall()
        return {int(r["user_id"]): int(r["manager_user_id"]) for r in rows}
    except sqlite3.Error as exc:
        logger.warning("get_manager_overrides failed, ignoring overrides: %s", exc)
        return {}


def set_manager_override(user_id: int, manager_user_id: int,
                         set_by_user_id: int, set_at: str) -> None:
    """Idempotently upsert an employee → manager override."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO admin_manager_overrides
                (user_id, manager_user_id, set_by_user_id, set_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                manager_user_id = excluded.manager_user_id,
                set_by_user_id  = excluded.set_by_user_id,
                set_at          = excluded.set_at
            """,
            (user_id, manager_user_id, set_by_user_id, set_at),
        )
        c.commit()


def reset_manager_overrides() -> int:
    """Delete every manager override (revert allocations to Classmate data).
    Returns the number of overrides cleared."""
    with _conn() as c:
        cur = c.execute("DELETE FROM admin_manager_overrides")
        c.commit()
        return cur.rowcount


# ── Exec overrides ──────────────────────────────────────────────────────────

def get_exec_overrides() -> dict:
    """Return {user_id: bool} for all exec-status overrides. Degrades to {} if
    the table is missing/unreadable — called per-request, must never raise."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT user_id, is_exec FROM admin_exec_overrides"
            ).fetchall()
        return {int(r["user_id"]): bool(r["is_exec"]) for r in rows}
    except sqlite3.Error as exc:
        logger.warning("get_exec_overrides failed, ignoring overrides: %s", exc)
        return {}


def set_exec_override(user_id: int, is_exec: bool,
                      set_by_user_id: int, set_at: str) -> None:
    """Idempotently upsert an exec-status override (True grants, False revokes)."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO admin_exec_overrides
                (user_id, is_exec, set_by_user_id, set_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                is_exec        = excluded.is_exec,
                set_by_user_id = excluded.set_by_user_id,
                set_at         = excluded.set_at
            """,
            (user_id, 1 if is_exec else 0, set_by_user_id, set_at),
        )
        c.commit()
