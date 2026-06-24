"""
nova_db/badges.py
SQLite-backed badge store — separate from Fabric.

Badges are awarded at the end of each month to every user whose tier is
above 'starter'. This module just persists and reads them; the monthly
award job is wired separately.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "nova_local.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_badges_table() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS user_badges (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                tier       TEXT    NOT NULL,
                awarded_at TEXT    NOT NULL,
                month      TEXT    NOT NULL,
                UNIQUE(user_id, month)
            )
            """
        )
        c.commit()


def get_user_badges(user_id: int) -> list[dict]:
    """Returns all badges for a user, newest month first."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT id, user_id, tier, awarded_at, month
            FROM user_badges
            WHERE user_id = ?
            ORDER BY month DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def award_badge(user_id: int, tier: str, month: str, awarded_at: str) -> None:
    """
    Idempotently award a badge for (user_id, month).
    month is 'YYYY-MM'; awarded_at is the ISO date the badge was granted.
    """
    with _conn() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO user_badges (user_id, tier, awarded_at, month)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, tier, awarded_at, month),
        )
        c.commit()
