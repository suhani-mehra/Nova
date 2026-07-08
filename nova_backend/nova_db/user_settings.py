"""
nova_db/user_settings.py
SQLite-backed per-account preferences — separate from Fabric.

Currently holds each user's color-mode (light/dark) default so the choice
follows the account across devices/logins rather than living only in the
browser. Mirrors the structure of nova_db/badges.py.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "nova_local.db"

_VALID_MODES = ("light", "dark")
_DEFAULT_MODE = "light"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_user_settings_table() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id    INTEGER PRIMARY KEY,
                color_mode TEXT    NOT NULL DEFAULT 'light',
                updated_at TEXT
            )
            """
        )
        c.commit()


def get_color_mode(user_id: int) -> str:
    """Return the user's saved color mode, or the light default if unset/invalid."""
    if user_id is None:
        return _DEFAULT_MODE
    with _conn() as c:
        row = c.execute(
            "SELECT color_mode FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    mode = row["color_mode"] if row else None
    return mode if mode in _VALID_MODES else _DEFAULT_MODE


def set_color_mode(user_id: int, mode: str, updated_at: str) -> None:
    """Idempotently upsert the user's color mode. Raises ValueError on bad input."""
    if mode not in _VALID_MODES:
        raise ValueError(f"invalid color_mode: {mode!r}")
    with _conn() as c:
        c.execute(
            """
            INSERT INTO user_settings (user_id, color_mode, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                color_mode = excluded.color_mode,
                updated_at = excluded.updated_at
            """,
            (user_id, mode, updated_at),
        )
        c.commit()
