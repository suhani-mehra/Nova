"""
nova_db/course_scores.py
Persistent SQLite store for per-course / per-certificate vertical scores.
"""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "nova_local.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_course_scores_table() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS course_vertical_scores (
                item_type  TEXT    NOT NULL,
                item_id    INTEGER NOT NULL,
                item_name  TEXT    NOT NULL,
                ai         INTEGER NOT NULL DEFAULT 0,
                cloud      INTEGER NOT NULL DEFAULT 0,
                frontend   INTEGER NOT NULL DEFAULT 0,
                backend    INTEGER NOT NULL DEFAULT 0,
                data       INTEGER NOT NULL DEFAULT 0,
                scored_at  TEXT    NOT NULL,
                PRIMARY KEY (item_type, item_id)
            )
        """)
        conn.commit()
    logger.info("course_vertical_scores table ready")


def get_scored_pairs() -> set[tuple[str, int]]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT item_type, item_id FROM course_vertical_scores"
            ).fetchall()
        return {(r["item_type"], r["item_id"]) for r in rows}
    except Exception as exc:
        logger.warning("get_scored_pairs failed: %s", exc)
        return set()


def get_scores_for_items(
    pairs: list[tuple[str, int]],
) -> dict[tuple[str, int], dict]:
    if not pairs:
        return {}
    result: dict[tuple[str, int], dict] = {}
    # Group by item_type so we can use simple item_id IN (...) queries
    by_type: dict[str, list[int]] = {}
    for item_type, item_id in pairs:
        by_type.setdefault(item_type, []).append(item_id)
    try:
        with _connect() as conn:
            for item_type, ids in by_type.items():
                for chunk_start in range(0, len(ids), 500):
                    chunk = ids[chunk_start : chunk_start + 500]
                    ph = ",".join("?" * len(chunk))
                    rows = conn.execute(
                        f"""
                        SELECT item_type, item_id, ai, cloud, frontend, backend, data
                        FROM course_vertical_scores
                        WHERE item_type = ? AND item_id IN ({ph})
                        """,
                        [item_type] + chunk,
                    ).fetchall()
                    for r in rows:
                        result[(r["item_type"], r["item_id"])] = {
                            "AI":       r["ai"],
                            "Cloud":    r["cloud"],
                            "Frontend": r["frontend"],
                            "Backend":  r["backend"],
                            "Data":     r["data"],
                        }
    except Exception as exc:
        logger.warning("get_scores_for_items failed: %s", exc)
    return result


def upsert_scores(rows: list[dict]) -> None:
    if not rows:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            conn.executemany(
                """
                INSERT INTO course_vertical_scores
                    (item_type, item_id, item_name, ai, cloud, frontend, backend, data, scored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_type, item_id) DO UPDATE SET
                    item_name = excluded.item_name,
                    ai        = excluded.ai,
                    cloud     = excluded.cloud,
                    frontend  = excluded.frontend,
                    backend   = excluded.backend,
                    data      = excluded.data,
                    scored_at = excluded.scored_at
                """,
                [
                    (
                        r["item_type"],
                        r["item_id"],
                        r["item_name"],
                        max(0, min(100, int(r.get("ai", 0)))),
                        max(0, min(100, int(r.get("cloud", 0)))),
                        max(0, min(100, int(r.get("frontend", 0)))),
                        max(0, min(100, int(r.get("backend", 0)))),
                        max(0, min(100, int(r.get("data", 0)))),
                        now,
                    )
                    for r in rows
                ],
            )
            conn.commit()
    except Exception as exc:
        logger.error("upsert_scores failed: %s", exc)


def get_scored_count() -> int:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM course_vertical_scores"
            ).fetchone()
            return row["n"] if row else 0
    except Exception:
        return 0
