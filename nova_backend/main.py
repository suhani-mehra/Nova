"""
main.py
Nova FastAPI application entry point.

Run with:
    uvicorn main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.auth import CurrentUser, get_current_user
from core.config import settings
from core.queries import get_employee_profile
from nova_db.congrats import init_db as init_congrats_db
from routers import employee, manager, congrats, auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _ensure_warehouse_ready():
    """
    Runs in a thread — the first-ever sync pulls ~570k rows from the API and
    must not run on the event loop.

    If nova_warehouse.db already holds a completed sync, this returns
    immediately (startup stays fast); the nightly job keeps it fresh.
    Only a cold start (no warehouse yet) blocks on a full sync.
    """
    from nova_db.warehouse_sync import sync_all, warehouse_is_ready, last_sync_time
    if warehouse_is_ready():
        logger.info("Warehouse ready (last sync: %s)", last_sync_time())
        return
    logger.info("Warehouse missing/incomplete — running initial sync from the Classmate API")
    sync_all()


# ── Lifespan: ensure the warehouse is ready on startup ───────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_congrats_db()
    from nova_db.gpt_cache import init_cache, clear_expired
    init_cache()
    clear_expired()
    logger.info("GPT cache initialised")
    from nova_db.course_scores import init_course_scores_table
    init_course_scores_table()
    from nova_db.tier_scores import init_tier_scores_table, refresh_tier_scores_cache
    init_tier_scores_table()
    from nova_db.badges import init_badges_table
    init_badges_table()
    from nova_db.user_settings import init_user_settings_table
    init_user_settings_table()
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _ensure_warehouse_ready)
        logger.info("Warehouse OK")
        print("\n✓ Warehouse OK\n")
        try:
            from routers.manager import _init_exec_users
            loop.run_in_executor(None, _init_exec_users)
        except Exception as exc:
            logger.warning("Could not schedule exec user lookup: %s", exc)
        try:
            from routers.manager import (
                _prewarm_classify_cache,
                _prewarm_streak_cache,
            )
            import time as _time

            def _staggered_prewarms():
                # Stagger so pre-warm jobs don't all hit Fabric simultaneously
                # alongside the other startup jobs (score_all_courses, ai_trend, etc.)
                _time.sleep(10)
                _prewarm_classify_cache()
                _time.sleep(5)
                _prewarm_streak_cache()

            loop.run_in_executor(None, _staggered_prewarms)
        except Exception as exc:
            logger.warning("Could not schedule cache pre-warms: %s", exc)
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
        try:
            from routers.manager import _compute_manager_team_snapshot
            loop.run_in_executor(None, _compute_manager_team_snapshot)
        except Exception as exc:
            logger.warning("Could not schedule team leaderboard snapshot job: %s", exc)
        try:
            from routers.manager import _compute_ai_proficiency_by_region
            loop.run_in_executor(None, _compute_ai_proficiency_by_region)
        except Exception as exc:
            logger.warning("Could not schedule AI proficiency-by-region job: %s", exc)
        try:
            # Pre-warm the expensive company-wide overview stats so the manager
            # overview page never blocks on a full company scan after a restart.
            from routers.manager import (
                _compute_company_overview_stats,
                _compute_company_retention,
                _compute_company_at_risk_count,
            )
            loop.run_in_executor(None, _compute_company_overview_stats)
            loop.run_in_executor(None, _compute_company_retention)
            loop.run_in_executor(None, _compute_company_at_risk_count)
        except Exception as exc:
            logger.warning("Could not schedule company overview stats warm-up: %s", exc)
        try:
            loop.run_in_executor(None, refresh_tier_scores_cache)
        except Exception as exc:
            logger.warning("Could not schedule tier score refresh: %s", exc)

        def _run_nightly_refresh():
            """
            Runs at 3 AM nightly (after the lakehouse ETL updates 12-2 AM).
            First re-syncs the local warehouse from the Classmate API, then
            refreshes all caches so they reflect the new day's data.
            Per-manager caches (people_list_*, direct_reports_*) are cleared
            so they rebuild fresh on first access rather than pre-warmed.
            """
            import time as _time
            from nova_db.gpt_cache import clear_by_prefix

            logger.info("Nightly cache refresh starting")

            # Pull fresh data from the API before any cache recomputation —
            # everything below reads the warehouse.
            try:
                from nova_db.warehouse_sync import sync_all
                sync_all()
            except Exception as exc:
                logger.error("Nightly warehouse sync failed (caches will rebuild "
                             "from the previous snapshot): %s", exc)

            # Month rollover: on the 1st (with a day<=3 backfill for a missed run),
            # award each employee the tier they ended the just-completed month with,
            # BEFORE the refresh below resets the live tier to the new month.
            try:
                from services.tier_service import award_monthly_badges
                from nova_db.badges import badges_exist_for
                today = datetime.now(timezone.utc).date()
                prior = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
                prior_str = prior.strftime("%Y-%m")
                if today.day == 1 or (today.day <= 3 and not badges_exist_for(prior_str)):
                    logger.info("Month rollover: awarding badges for %s", prior_str)
                    award_monthly_badges(prior, awarded_at=today.isoformat())
            except Exception as exc:
                logger.warning("Nightly monthly badge award failed: %s", exc)

            try:
                refresh_tier_scores_cache(force=True)
            except Exception as exc:
                logger.warning("Nightly tier score refresh failed: %s", exc)

            _time.sleep(5)
            try:
                from routers.manager import _prewarm_classify_cache
                _prewarm_classify_cache()
            except Exception as exc:
                logger.warning("Nightly classify pre-warm failed: %s", exc)

            _time.sleep(5)
            try:
                from routers.manager import _prewarm_streak_cache
                _prewarm_streak_cache()
            except Exception as exc:
                logger.warning("Nightly streak pre-warm failed: %s", exc)

            _time.sleep(5)
            try:
                from routers.manager import (
                    _compute_company_overview_stats,
                    _compute_company_retention,
                    _compute_company_at_risk_count,
                    _compute_quarterly_ai_proficiency,
                    _compute_manager_team_snapshot,
                    _compute_ai_proficiency_by_region,
                )
                _compute_company_overview_stats()
                _compute_company_retention()
                _compute_company_at_risk_count()
                _compute_quarterly_ai_proficiency()
                _compute_manager_team_snapshot()
                _compute_ai_proficiency_by_region()
            except Exception as exc:
                logger.warning("Nightly company stats refresh failed: %s", exc)

            # Clear stale per-manager caches then pre-warm all managers
            clear_by_prefix("people_list_")
            clear_by_prefix("direct_reports_")
            try:
                from routers.manager import _prewarm_manager_people_cache
                _prewarm_manager_people_cache()
            except Exception as exc:
                logger.warning("Nightly manager people pre-warm failed: %s", exc)
            logger.info("Nightly cache refresh complete")

        async def _nightly_refresh_loop():
            while True:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
                if now >= next_run:
                    next_run += timedelta(days=1)
                await asyncio.sleep((next_run - now).total_seconds())
                try:
                    loop.run_in_executor(None, _run_nightly_refresh)
                except Exception as exc:
                    logger.warning("Nightly cache refresh failed to schedule: %s", exc)

        asyncio.ensure_future(_nightly_refresh_loop())
    except Exception as exc:
        logger.error("Warehouse init FAILED on startup: %s", exc)
        print(f"\n✗ Warehouse init FAILED: {exc}\n")
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
    from nova_db.warehouse_sync import last_sync_time
    return {
        "status": "ok",
        "env": settings.nova_env,
        "model": settings.openai_model,
        "warehouse_last_sync": last_sync_time(),
    }


@app.post("/api/admin/sync")
async def admin_sync(user: CurrentUser = Depends(get_current_user)):
    """
    Manually re-sync the local warehouse from the Classmate API.
    Runs in a worker thread (~1-2 min); readers keep the old snapshot until
    the atomic file swap at the end.
    """
    from nova_db.warehouse_sync import sync_all
    loop = asyncio.get_event_loop()
    try:
        counts = await loop.run_in_executor(None, sync_all)
    except Exception as exc:
        logger.error("Manual warehouse sync failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}")
    return {"ok": True, "tables": counts}


@app.get("/api/me")
def me(user: CurrentUser = Depends(get_current_user)):
    """
    Returns the current user's profile.

    Dev mode (AZURE_TENANT_ID=placeholder): returns stub user without hitting DB.
    Post-AD: resolves classmate_user_id → dim_employee_profile row.
    """
    from nova_db.user_settings import get_color_mode
    is_exec_manager = manager._is_exec_manager(user)
    color_mode = get_color_mode(user.classmate_user_id) if user.classmate_user_id else "light"

    if user.classmate_user_id is None:
        # Production path before Classmate user lookup is wired
        return {
            "user_id":          None,
            "name":             user.name,
            "email":            user.email,
            "role":             user.role,
            "is_exec_manager":  is_exec_manager,
            "color_mode":       color_mode,
            "department_code":  None,
            "designation_code": None,
            "employee_id":      None,
            "manager_id":       None,
        }

    try:
        rows = get_employee_profile(None, user.classmate_user_id)
    except Exception as exc:
        logger.warning("/api/me warehouse lookup failed, returning auth data: %s", exc)
        rows = []

    if rows:
        p = rows[0]
        return {
            "user_id":          p["user_id"],
            "name":             p["name"],
            "email":            p["email_id"],
            "role":             user.role,
            "is_exec_manager":  is_exec_manager,
            "color_mode":       color_mode,
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
        "is_exec_manager":  is_exec_manager,
        "color_mode":       color_mode,
        "department_code":  None,
        "designation_code": None,
        "employee_id":      None,
        "manager_id":       None,
    }


@app.post("/api/me/color-mode")
def set_me_color_mode(payload: dict, user: CurrentUser = Depends(get_current_user)):
    """Persist the current account's light/dark preference."""
    from datetime import datetime, timezone
    from nova_db.user_settings import set_color_mode
    if user.classmate_user_id is None:
        raise HTTPException(status_code=503, detail="No user identity")
    mode = (payload or {}).get("mode")
    if mode not in ("light", "dark"):
        raise HTTPException(status_code=400, detail="mode must be 'light' or 'dark'")
    set_color_mode(user.classmate_user_id, mode, datetime.now(timezone.utc).isoformat())
    return {"ok": True, "color_mode": mode}


# Serve frontend — must come after all API routes.
# NoCacheStaticFiles forces the browser to always refetch, so edits to the
# .js/.jsx/.css files show up on a normal reload (no manual ?v= bumping).
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "nova_frontend"
app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
