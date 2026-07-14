"""
nova_db/congrats.py
SQLite-backed congrats store — separate from Fabric.
"""

import sqlite3
from pathlib import Path

from core.config import settings

_DB_PATH = Path(settings.nova_local_db_path)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS congrats (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_user_id   INTEGER NOT NULL,
                receiver_user_id INTEGER NOT NULL,
                activity_id      INTEGER NOT NULL,
                message          TEXT    NOT NULL,
                created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def save_congrats(sender_user_id: int, receiver_user_id: int, activity_id: int, message: str) -> int:
    """Record a congrats. Idempotent on (sender, receiver, activity_id) so a user
    re-congratulating the same accomplishment doesn't inflate the recipient's count.
    Returns the row id (existing one if it was already recorded)."""
    with _get_conn() as conn:
        existing = conn.execute(
            """
            SELECT id FROM congrats
            WHERE sender_user_id = ? AND receiver_user_id = ? AND activity_id = ?
            """,
            (sender_user_id, receiver_user_id, activity_id),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            """
            INSERT INTO congrats (sender_user_id, receiver_user_id, activity_id, message)
            VALUES (?, ?, ?, ?)
            """,
            (sender_user_id, receiver_user_id, activity_id, message),
        )
        conn.commit()
        return cur.lastrowid


def get_congrats_received_count(user_id: int) -> int:
    """All-time count of congrats received by user_id."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM congrats WHERE receiver_user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def get_congrats_for_user(user_id: int, days: int = 7) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, sender_user_id, receiver_user_id, activity_id,
                   message, created_at
            FROM   congrats
            WHERE  receiver_user_id = ?
              AND  created_at >= datetime('now', ? || ' days')
            ORDER BY created_at DESC
            """,
            (user_id, f"-{days}"),
        ).fetchall()
    return [dict(r) for r in rows]
