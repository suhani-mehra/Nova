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


_TIER_KEYS = ("platinum", "diamond", "gold", "silver", "bronze")


def get_team_badge_summary(user_ids: list[int], this_month: str | None = None) -> dict:
    """
    Aggregate badge stats for a set of users — for the manager "Your Team" page.

    Returns:
        {
          "total":            <all-time badge rows across these users>,
          "by_tier":          {platinum, diamond, gold, silver, bronze},
          "this_month_count": <rows whose month == this_month 'YYYY-MM'>,
        }
    `this_month` defaults to the current calendar month (UTC). starter is never
    awarded, so it's not in by_tier. Single IN(...) query, not a per-user loop.
    """
    by_tier = {k: 0 for k in _TIER_KEYS}
    if not user_ids:
        return {"total": 0, "by_tier": by_tier, "this_month_count": 0}

    if this_month is None:
        from datetime import datetime, timezone
        this_month = datetime.now(timezone.utc).strftime("%Y-%m")

    ph = ",".join("?" * len(user_ids))
    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT tier, month, COUNT(*) AS n
            FROM user_badges
            WHERE user_id IN ({ph})
            GROUP BY tier, month
            """,
            tuple(user_ids),
        ).fetchall()

    total = 0
    this_month_count = 0
    for r in rows:
        n = int(r["n"])
        total += n
        tier = (r["tier"] or "").lower()
        if tier in by_tier:
            by_tier[tier] += n
        if r["month"] == this_month:
            this_month_count += n

    return {"total": total, "by_tier": by_tier, "this_month_count": this_month_count}


def badges_exist_for(month: str) -> bool:
    """True if any badge has been awarded for the given 'YYYY-MM' month.
    Used by the nightly job to gate/backfill the monthly award."""
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM user_badges WHERE month = ? LIMIT 1", (month,)
        ).fetchone()
    return row is not None
