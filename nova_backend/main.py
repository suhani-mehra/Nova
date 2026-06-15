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

from core.auth import CurrentUser, get_current_user
from core.config import settings
from core.database import get_connection
from core.queries import get_employee_profile

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
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _test_fabric_connection)
        logger.info("Fabric connection OK")
        print("\n✓ Fabric connection OK\n")
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
        # Dev bypass — no DB call
        return {
            "user_id": None,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "azure_oid": user.azure_oid,
            "department_code": None,
            "designation_code": None,
            "employee_id": None,
            "manager_id": None,
            "dev_mode": True,
        }

    conn = get_connection()
    rows = get_employee_profile(conn, user.classmate_user_id)

    if not rows:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee profile not found in Classmate",
        )

    p = rows[0]
    return {
        "user_id": p["user_id"],
        "name": p["display_name"] or f"{p['first_name']} {p['last_name']}",
        "email": p["email_id"],
        "role": user.role,
        "department_code": p["department_code"],
        "designation_code": p["designation_code"],
        "employee_id": p["employee_id"],
        "manager_id": p["manager"],
        "dev_mode": False,
    }
