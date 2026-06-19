"""
main.py
Nova FastAPI application entry point.

Run with:
    uvicorn main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.auth import CurrentUser, get_current_user
from core.config import settings
from core.database import get_connection
from core.queries import get_employee_profile
from nova_db.congrats import init_db as init_congrats_db
from routers import employee, manager, congrats, auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _test_fabric_connection():
    """Runs in a thread — pyodbc.connect() blocks and must not run on the event loop."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    cursor.fetchone()


# ── Lifespan: test Fabric connection on startup ───────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_congrats_db()
    from nova_db.gpt_cache import init_cache, clear_expired
    init_cache()
    clear_expired()
    logger.info("GPT cache initialised")
    from nova_db.course_scores import init_course_scores_table
    init_course_scores_table()
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _test_fabric_connection)
        logger.info("Fabric connection OK")
        print("\n✓ Fabric connection OK\n")
        try:
            from routers.manager import _init_exec_users
            loop.run_in_executor(None, _init_exec_users)
        except Exception as exc:
            logger.warning("Could not schedule exec user lookup: %s", exc)
        try:
            from services.course_scoring_service import score_all_courses
            loop.run_in_executor(None, score_all_courses)
        except Exception as exc:
            logger.warning("Could not schedule course scoring job: %s", exc)
        try:
            from routers.manager import _compute_quarterly_ai_proficiency
            loop.run_in_executor(None, _compute_quarterly_ai_proficiency)
        except Exception as exc:
            logger.warning("Could not schedule AI trend job: %s", exc)
    except Exception as exc:
        logger.error("Fabric connection FAILED on startup: %s", exc)
        print(f"\n✗ Fabric connection FAILED: {exc}\n")
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nova",
    description="Orion Innovation internal employee learning dashboard API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.nova_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(employee.router, prefix="/api")
app.include_router(manager.router, prefix="/api")
app.include_router(congrats.router, prefix="/api")
app.include_router(auth.router,     prefix="/api")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/ping")
def ping():
    """Health check — confirms the server is up and shows active config."""
    return {
        "status": "ok",
        "env": settings.nova_env,
        "model": settings.openai_model,
    }


@app.get("/api/me")
def me(user: CurrentUser = Depends(get_current_user)):
    """
    Returns the current user's profile.

    Dev mode (AZURE_TENANT_ID=placeholder): returns stub user without hitting DB.
    Post-AD: resolves classmate_user_id → dim_employee_profile row.
    """
    if user.classmate_user_id is None:
        # Production path before Classmate user lookup is wired
        return {
            "user_id":          None,
            "name":             user.name,
            "email":            user.email,
            "role":             user.role,
            "department_code":  None,
            "designation_code": None,
            "employee_id":      None,
            "manager_id":       None,
        }

    try:
        conn = get_connection()
        rows = get_employee_profile(conn, user.classmate_user_id)
    except Exception as exc:
        logger.warning("/api/me Fabric lookup failed, returning auth data: %s", exc)
        rows = []

    if rows:
        p = rows[0]
        return {
            "user_id":          p["user_id"],
            "name":             p["name"],
            "email":            p["email_id"],
            "role":             user.role,
            "department_code":  p["department"],
            "designation_code": p["designation"],
            "employee_id":      p["employee_id"],
            "manager_id":       p["manager_user_id"],
        }

    # Fabric unreachable or profile not found — return identity from auth layer
    return {
        "user_id":          user.classmate_user_id,
        "name":             user.name,
        "email":            user.email,
        "role":             user.role,
        "department_code":  None,
        "designation_code": None,
        "employee_id":      None,
        "manager_id":       None,
    }


# Serve frontend — must come after all API routes
app.mount("/", StaticFiles(directory="static", html=True), name="static")
