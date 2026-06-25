import sqlite3, json, logging
from datetime import datetime, timedelta
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "nova_local.db"
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

def get_cache(key: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT result, scored_by FROM gpt_cache "
            "WHERE cache_key=? AND expires_at > datetime('now')",
            (key,)
        ).fetchone()
    if not row:
        return None
    try:
        return {"result": json.loads(row["result"]),
                "scored_by": row["scored_by"]}
    except Exception:
        return None

def get_cache_stale(key: str) -> dict | None:
    """Like get_cache but ignores expiry — for stale-while-revalidate reads."""
    with _conn() as c:
        row = c.execute(
            "SELECT result, scored_by FROM gpt_cache WHERE cache_key=?",
            (key,)
        ).fetchone()
    if not row:
        return None
    try:
        return {"result": json.loads(row["result"]),
                "scored_by": row["scored_by"]}
    except Exception:
        return None

def set_cache(key: str, result: dict,
              scored_by: str, ttl_hours: int = 24):
    expires = (datetime.utcnow() +
               timedelta(hours=ttl_hours)).isoformat()
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
