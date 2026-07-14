import sqlite3, json, logging
from datetime import datetime, timedelta
from pathlib import Path

from core.config import settings

_DB_PATH = Path(settings.nova_local_db_path)
logger = logging.getLogger(__name__)

def _conn():
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c

def init_cache():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS gpt_cache (
            cache_key  TEXT PRIMARY KEY,
            result     TEXT NOT NULL,
            scored_by  TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )""")
        c.commit()

def _row_to_cache_result(row, key: str, fn_name: str) -> dict | None:
    """Shared row->result decode for get_cache/get_cache_stale: guards a
    missing row and decodes the JSON result, logging (with fn_name for
    traceability) and treating corrupted JSON as a cache miss rather than
    raising."""
    if not row:
        return None
    try:
        return {"result": json.loads(row["result"]),
                "scored_by": row["scored_by"]}
    except Exception as exc:
        logger.warning("%s: corrupted cache row for key=%s: %s", fn_name, key, exc)
        return None

def get_cache(key: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT result, scored_by FROM gpt_cache "
            "WHERE cache_key=? AND expires_at > datetime('now')",
            (key,)
        ).fetchone()
    return _row_to_cache_result(row, key, "get_cache")

def get_cache_stale(key: str) -> dict | None:
    """Like get_cache but ignores expiry — for stale-while-revalidate reads."""
    with _conn() as c:
        row = c.execute(
            "SELECT result, scored_by FROM gpt_cache WHERE cache_key=?",
            (key,)
        ).fetchone()
    return _row_to_cache_result(row, key, "get_cache_stale")

def set_cache(key: str, result: dict,
              scored_by: str, ttl_hours: int = 24):
    # Always expire at 4 AM UTC the following day, regardless of ttl_hours.
    # Using "tomorrow at 4 AM" ensures entries written by the 3 AM nightly
    # refresh get a full ~25h TTL rather than expiring 55 min later.
    now = datetime.utcnow()
    expires = (now + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
    expires = expires.isoformat(sep=" ")
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO gpt_cache "
            "VALUES (?,?,?,?)",
            (key, json.dumps(result), scored_by, expires)
        )
        c.commit()

def clear_expired():
    with _conn() as c:
        c.execute(
            "DELETE FROM gpt_cache "
            "WHERE expires_at <= datetime('now')"
        )
        c.commit()

def clear_by_prefix(prefix: str) -> int:
    """Delete all cache entries whose key starts with prefix. Returns count deleted."""
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM gpt_cache WHERE cache_key LIKE ?",
            (prefix + "%",)
        )
        c.commit()
        return cur.rowcount
