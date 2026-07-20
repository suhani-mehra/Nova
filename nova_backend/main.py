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
from routers import employee, manager, congrats, auth, admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _schedule_job(loop, module_path: str, fn_name: str, label: str) -> None:
    """Fire-and-forget: import `fn_name` from `module_path` and dispatch it to
    the executor. Shared shape for the startup pre-warm jobs in lifespan()
    (previously ~10 near-identical try/import/schedule/except-log blocks).
    Logs (tagged with `label`) if either the import or the scheduling itself
    fails, matching each job's original independent try/except."""
    try:
        import importlib
        fn = getattr(importlib.import_module(module_path), fn_name)
        loop.run_in_executor(None, fn)
    except Exception as exc:
        logger.warning("Could not schedule %s job: %s", label, exc)


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


def _init_startup_tables() -> None:
    """One-time schema/table initialization for every SQLite-backed cache the
    app uses, run once at the start of lifespan() before anything else."""
    init_congrats_db()
    from nova_db.gpt_cache import init_cache, clear_expired
    init_cache()
    clear_expired()
    logger.info("GPT cache initialised")
    from nova_db.course_scores import init_course_scores_table, get_scored_count
    init_course_scores_table()
    # Visibility: surface the seeded course-score count at boot. A 0 here on a
    # production deploy means nova_local.db was not seeded — catch it before the
    # scoring job (or its disabled backstop) runs, rather than discovering a
    # full rescore after the fact.
    _scored = get_scored_count()
    if _scored == 0:
        logger.warning("course scores: 0 courses scored — nova_local.db appears unseeded (path=%s)",
                       settings.nova_local_db_path)
    else:
        logger.info("course scores: %d courses already scored (path=%s)",
                    _scored, settings.nova_local_db_path)
    from nova_db.tier_scores import init_tier_scores_table
    init_tier_scores_table()
    from nova_db.badges import init_badges_table
    init_badges_table()
    from nova_db.user_settings import init_user_settings_table
    init_user_settings_table()
    from nova_db.admin_overrides import init_admin_overrides_tables
    init_admin_overrides_tables()


def _schedule_prewarm_jobs(loop) -> None:
    """Fire-and-forget schedule every startup cache pre-warm / compute job onto
    the executor. Each job's own try/except (via _schedule_job, or inline for
    the staggered/grouped jobs below) means a single job failing never blocks
    or fails the others."""
    try:
        from routers.manager import _prewarm_classify_cache, _prewarm_streak_cache
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
    _schedule_job(loop, "services.course_scoring_service", "score_all_courses", "course scoring")
    _schedule_job(loop, "routers.manager", "_compute_quarterly_ai_proficiency", "AI trend")
    _schedule_job(loop, "routers.manager", "_compute_manager_team_snapshot", "team leaderboard snapshot")
    _schedule_job(loop, "routers.manager", "_compute_ai_proficiency_by_region", "AI proficiency-by-region")
    _schedule_job(loop, "routers.manager", "_compute_proficiency_by_vertical", "proficiency-by-vertical")
    _schedule_job(loop, "routers.manager", "_compute_specialization_landscape", "specialization-landscape")
    _schedule_job(loop, "routers.manager", "_compute_team_quadrant", "team-quadrant")
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
        from nova_db.tier_scores import refresh_tier_scores_cache
        loop.run_in_executor(None, refresh_tier_scores_cache)
    except Exception as exc:
        logger.warning("Could not schedule tier score refresh: %s", exc)


def _run_nightly_refresh() -> None:
    """
    Runs at 3 AM nightly (after the lakehouse ETL updates 12-2 AM).
    First re-syncs the local warehouse from the Classmate API, then
    refreshes all caches so they reflect the new day's data.
    Per-manager caches (people_list_*, direct_reports_*) are cleared
    so they rebuild fresh on first access rather than pre-warmed.
    """
    import time as _time
    from nova_db.gpt_cache import clear_by_prefix
    from nova_db.tier_scores import refresh_tier_scores_cache

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
            _compute_proficiency_by_vertical,
            _compute_specialization_landscape,
            _compute_team_quadrant,
        )
        _compute_company_overview_stats()
        _compute_company_retention()
        _compute_company_at_risk_count()
        _compute_quarterly_ai_proficiency()
        _compute_manager_team_snapshot()
        _compute_ai_proficiency_by_region()
        _compute_proficiency_by_vertical()
        _compute_specialization_landscape()
        _compute_team_quadrant()
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


async def _nightly_refresh_loop(loop) -> None:
    """Sleeps until the next 3 AM UTC, dispatches _run_nightly_refresh to the
    executor, then repeats forever. `loop` is passed explicitly (rather than
    re-fetched via asyncio.get_event_loop()) so it's always the same loop
    instance lifespan() started with."""
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


# ── Lifespan: ensure the warehouse is ready on startup ───────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_startup_tables()
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _ensure_warehouse_ready)
        logger.info("Warehouse OK")
        print("\n✓ Warehouse OK\n")
        _schedule_prewarm_jobs(loop)
        asyncio.ensure_future(_nightly_refresh_loop(loop))
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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# Content-Security-Policy tuned to the app's actual external origins so nothing
# breaks: unpkg (React/Babel), FontAwesome kit, Google Fonts, and the MSAL login
# host (script/frame/connect for silent-token iframes). 'unsafe-eval' is required
# by the in-browser Babel transform; inline React style objects are set via the
# DOM style property and are not affected by style-src.
_CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com https://ka-f.fontawesome.com; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://kit.fontawesome.com https://ka-f.fontawesome.com; "
    "connect-src 'self' https://login.microsoftonline.com https://login.windows.net https://ka-f.fontawesome.com; "
    "frame-src https://login.microsoftonline.com"
)
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": _CSP,
}


@app.middleware("http")
async def add_security_headers(request, call_next):
    """Attach standard security headers to every response (defense in depth)."""
    response = await call_next(request)
    for key, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


app.include_router(employee.router, prefix="/api")
app.include_router(manager.router, prefix="/api")
app.include_router(congrats.router, prefix="/api")
app.include_router(auth.router,     prefix="/api")
app.include_router(admin.router,    prefix="/api")


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
    the atomic file swap at the end. Restricted to exec managers — this is a
    heavy, ops-only operation.
    """
    if not manager._is_exec_manager(user):
        raise HTTPException(status_code=403, detail="Executive manager access required")
    from nova_db.warehouse_sync import sync_all
    loop = asyncio.get_event_loop()
    try:
        counts = await loop.run_in_executor(None, sync_all)
    except Exception as exc:
        logger.error("Manual warehouse sync failed: %s", exc)
        raise HTTPException(status_code=502, detail="Warehouse sync failed")
    return {"ok": True, "tables": counts}


def _build_me_response(user_id, name, email, role, is_exec_manager, is_admin, color_mode,
                        department_code, designation_code, employee_id, manager_id) -> dict:
    """Shared shape for /api/me's three response branches (no identity yet,
    profile found, profile lookup failed/empty) — same keys, different
    sources."""
    return {
        "user_id":          user_id,
        "name":             name,
        "email":            email,
        "role":             role,
        "is_exec_manager":  is_exec_manager,
        "is_admin":         is_admin,
        "color_mode":       color_mode,
        "department_code":  department_code,
        "designation_code": designation_code,
        "employee_id":      employee_id,
        "manager_id":       manager_id,
    }


@app.get("/api/me")
def me(user: CurrentUser = Depends(get_current_user)):
    """
    Returns the current user's profile.

    Dev mode (AZURE_TENANT_ID=placeholder): returns stub user without hitting DB.
    Post-AD: resolves classmate_user_id → dim_employee_profile row.
    """
    from nova_db.user_settings import get_color_mode
    is_exec_manager = manager._is_exec_manager(user)
    is_admin = admin._is_admin(user)
    color_mode = get_color_mode(user.classmate_user_id) if user.classmate_user_id else "light"

    if user.classmate_user_id is None:
        # Production path before Classmate user lookup is wired
        return _build_me_response(
            None, user.name, user.email, user.role, is_exec_manager, is_admin, color_mode,
            None, None, None, None)

    try:
        rows = get_employee_profile(None, user.classmate_user_id)
    except Exception as exc:
        logger.warning("/api/me warehouse lookup failed, returning auth data: %s", exc)
        rows = []

    if rows:
        p = rows[0]
        return _build_me_response(
            p["user_id"], p["name"], p["email_id"], user.role, is_exec_manager, is_admin, color_mode,
            p["department"], p["designation"], p["employee_id"], p["manager_user_id"])

    # Fabric unreachable or profile not found — return identity from auth layer
    return _build_me_response(
        user.classmate_user_id, user.name, user.email, user.role, is_exec_manager, is_admin, color_mode,
        None, None, None, None)


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
