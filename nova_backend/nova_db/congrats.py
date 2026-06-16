"""
nova_db/congrats.py
SQLite-backed congrats store — separate from Fabric.
"""

import sqlite3
import os
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "nova_local.db"


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
    with _get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO congrats (sender_user_id, receiver_user_id, activity_id, message)
            VALUES (?, ?, ?, ?)
            """,
            (sender_user_id, receiver_user_id, activity_id, message),
        )
        conn.commit()
        return cur.lastrowid


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
