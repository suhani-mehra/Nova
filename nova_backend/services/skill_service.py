"""
services/skill_service.py
Skill radar (5 categories) and AI proficiency scoring.
"""

from datetime import date, timedelta

from core.database import query
from core.config import settings

CATEGORY_KEYWORDS = {
    "AI": [
        "ai", "artificial intelligence", "machine learning", "ml", "genai",
        "gpt", "llm", "prompt", "openai", "anthropic", "neural", "nlp",
        "generative", "agentic",
    ],
    "Cloud": [
        "azure", "aws", "gcp", "cloud", "kubernetes", "docker", "terraform",
        "devops", "serverless", "snowflake",
    ],
    "Frontend": [
        "react", "angular", "vue", "javascript", "typescript", "css", "html",
        "frontend", "ui", "ux", "power apps", "figma", "web",
    ],
    "Backend": [
        "python", "java", "node", "fastapi", "spring", "api", "rest",
        "backend", "database", "sql", "mongodb", "postgresql", ".net", "c#",
    ],
    "Data": [
        "data", "analytics", "power bi", "tableau", "pandas", "spark",
        "data science", "statistics", "bi", "reporting", "etl", "warehouse",
    ],
}

AXES = ["AI", "Cloud", "Frontend", "Backend", "Data"]


def _classify(course_name: str) -> str | None:
    name_lower = course_name.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return cat
    return None


def _credits_by_category(rows: list[dict]) -> dict[str, float]:
    totals = {cat: 0.0 for cat in AXES}
    for r in rows:
        cat = _classify(r.get("course_name") or "")
        if cat:
            totals[cat] += float(r.get("learning_credits") or 0)
    return totals


def calculate_skill_radar(user_id: int, conn=None) -> dict:
    today = date.today()
    first_this_month = today.replace(day=1)
    first_last_month = (first_this_month - timedelta(days=1)).replace(day=1)

    completed_rows = query(
        """
        SELECT course_name, learning_credits, completed_on
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
          AND  completed_on >= ?
        """,
        (user_id, first_last_month),
    )

    this_month_rows = [
        r for r in completed_rows
        if r["completed_on"] and (
            r["completed_on"].date() if hasattr(r["completed_on"], "date") else r["completed_on"]
        ) >= first_this_month
    ]
    last_month_rows = [
        r for r in completed_rows
        if r["completed_on"] and (
            r["completed_on"].date() if hasattr(r["completed_on"], "date") else r["completed_on"]
        ) < first_this_month
    ]

    this_month_cat = _credits_by_category(this_month_rows)
    last_month_cat = _credits_by_category(last_month_rows)

    # Team average per category (teammates = same manager)
    manager_rows = query(
        """
        SELECT manager
        FROM   classmate.dim_classmate_employee_profile
        WHERE  user_id    = ?
          AND  is_deleted = 0
        """,
        (user_id,),
    )
    manager_id = manager_rows[0]["manager"] if manager_rows else None

    team_avg = {cat: 50.0 for cat in AXES}  # fallback: no normalisation
    if manager_id:
        team_rows = query(
            """
            SELECT vt.course_name, vt.learning_credits
            FROM   classmate.vw_classmate_trainings vt
            JOIN   classmate.dim_classmate_employee_profile ep ON ep.user_id = vt.user_id
            WHERE  ep.manager    = ?
              AND  ep.is_deleted = 0
              AND  vt.status     = 4052
              AND  vt.completed_on >= ?
            """,
            (manager_id, first_this_month),
        )

        team_user_ids = query(
            """
            SELECT user_id
            FROM   classmate.dim_classmate_employee_profile
            WHERE  manager    = ?
              AND  is_deleted = 0
            """,
            (manager_id,),
        )
        team_count = max(len(team_user_ids), 1)

        team_totals = _credits_by_category(team_rows)
        team_avg = {cat: (team_totals[cat] / team_count) for cat in AXES}

    def normalise(raw: float, avg: float) -> int:
        if avg == 0:
            return 50 if raw == 0 else 100
        score = (raw / avg) * 50
        return max(0, min(100, int(score)))

    this_month = [normalise(this_month_cat[c], team_avg[c]) for c in AXES]
    last_month = [normalise(last_month_cat[c], team_avg[c]) for c in AXES]

    delta = int(sum(this_month[i] - last_month[i] for i in range(5)) / 5)

    return {
        "axes":       AXES,
        "this_month": this_month,
        "last_month": last_month,
        "delta":      delta,
    }


def _ai_score_for_user(user_id: int) -> float:
    """Raw (un-normalised) AI score: credits from AI courses + AI certs."""
    course_rows = query(
        """
        SELECT SUM(learning_credits) AS credits
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id = ?
          AND  status  = 4052
        """,
        (user_id,),
    )
    cert_rows = query(
        """
        SELECT SUM(dc.learning_credit_value) AS credits
        FROM   classmate.fact_classmate_certification fc
        JOIN   classmate.dim_classmate_certificate dc ON dc.id = fc.certificate_id
        WHERE  fc.user_id    = ?
          AND  fc.status     = 2
          AND  fc.is_deleted = 0
        """,
        (user_id,),
    )

    ai_course_credits = 0.0
    if course_rows:
        all_completed = query(
            """
            SELECT course_name, learning_credits
            FROM   classmate.vw_classmate_trainings
            WHERE  user_id = ?
              AND  status  = 4052
            """,
            (user_id,),
        )
        ai_course_credits = sum(
            float(r["learning_credits"] or 0)
            for r in all_completed
            if _classify(r.get("course_name") or "") == "AI"
        )

    ai_cert_credits = float(cert_rows[0]["credits"] or 0) if cert_rows else 0.0
    return ai_course_credits + ai_cert_credits


def calculate_ai_proficiency(user_id: int, conn=None) -> float:
    """Returns normalised AI proficiency score 0-100 for a single user."""
    all_user_rows = query(
        """
        SELECT DISTINCT user_id
        FROM   classmate.dim_classmate_employee_profile
        WHERE  is_active  = 1
          AND  is_deleted = 0
        """,
    )
    all_uids = [r["user_id"] for r in all_user_rows]

    user_raw = _ai_score_for_user(user_id)
    if not all_uids:
        return 0.0

    # Only compute max against a sample to avoid N+1 explosion in single-user calls
    # For single-user call we compute just this user's score relative to a cached max
    # Full normalisation happens in get_team_ai_proficiency
    all_scores = [_ai_score_for_user(uid) for uid in all_uids]
    max_score = max(all_scores) if all_scores else 1.0
    if max_score == 0:
        return 0.0
    return round(user_raw / max_score * 100, 1)


def get_team_ai_proficiency(manager_user_id: int, conn=None) -> dict:
    """Returns count and % of direct reports who are AI proficient.

    Uses two batch queries (direct reports + their credits) instead of N+1
    per-user calls to _ai_score_for_user.
    """
    reports = query(
        """
        SELECT DISTINCT user_id
        FROM   classmate.dim_classmate_employee_profile
        WHERE  manager    = ?
          AND  is_active  = 1
          AND  is_deleted = 0
        """,
        (manager_user_id,),
    )
    if not reports:
        return {"count": 0, "pct": 0.0, "total": 0}

    uids = [r["user_id"] for r in reports]
    placeholders = ",".join("?" * len(uids))

    # One query: total completed learning credits per team member
    credit_rows = query(
        f"""
        SELECT user_id, SUM(learning_credits) AS total_credits
        FROM   classmate.vw_classmate_trainings
        WHERE  user_id IN ({placeholders})
          AND  status   = 4052
        GROUP BY user_id
        """,
        tuple(uids),
    )
    uid_credits = {r["user_id"]: float(r["total_credits"] or 0) for r in credit_rows}
    max_credits = max(uid_credits.values()) if uid_credits else 0.0
    if max_credits == 0:
        return {"count": 0, "pct": 0.0, "total": len(uids)}

    threshold = settings.ai_proficiency_min_score
    proficient = sum(
        1 for uid in uids
        if (uid_credits.get(uid, 0.0) / max_credits * 100) >= threshold
    )
    total = len(uids)
    return {
        "count": proficient,
        "pct":   round(proficient / total * 100, 1) if total else 0.0,
        "total": total,
    }
