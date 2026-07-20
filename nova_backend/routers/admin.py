"""
routers/admin.py
Admin-only endpoints for the configured admins (ADMIN_USER_IDS, .env).

Lets admins override Classmate-derived manager allocations and exec status so
manager / exec views can be exercised as themselves — no impersonation. Every
route fails closed (403) for non-admins, mirroring the /api/admin/sync gate in
main.py. Overrides persist in nova_local.db (nova_db/admin_overrides) and are
layered over Classmate data by core.queries (manager allocation) and
routers.manager._is_exec_manager (exec status).

Included under the /api prefix in main.py, so routes are /api/admin/*.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.auth import CurrentUser, get_current_user
from core.config import settings
from core.queries import get_user_by_id, get_employee_profile, is_manager
from nova_db.admin_overrides import (
    get_manager_overrides, set_manager_override, reset_manager_overrides,
    get_exec_overrides, set_exec_override,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Admins allowed to override allocations / exec status. Sourced from .env
# (ADMIN_USER_IDS) so no privileged IDs are hardcoded in scanned source.
ADMIN_USER_IDS: set[int] = set(settings.admin_user_ids)


def _is_admin(user: CurrentUser) -> bool:
    """True only for the configured admins (ADMIN_USER_IDS)."""
    return user.classmate_user_id in ADMIN_USER_IDS


def _require_admin(user: CurrentUser) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_name(user_id: int) -> str:
    """Best-effort display name for a user_id (falls back to '#<id>')."""
    try:
        rows = get_employee_profile(None, user_id)
        if rows and rows[0].get("name"):
            return rows[0]["name"]
    except Exception as exc:
        logger.warning("admin _display_name lookup failed for %s: %s", user_id, exc)
    return f"#{user_id}"


def _clear_manager_caches() -> None:
    """Drop the caches that hold override-applied team data so an allocation
    change is reflected on the next read (the underlying direct_reports_ cache
    holds only Classmate data and is left intact)."""
    try:
        from nova_db.gpt_cache import clear_by_prefix
        clear_by_prefix("people_list_")
        clear_by_prefix("your_team_v3_")
    except Exception as exc:
        logger.warning("admin _clear_manager_caches failed: %s", exc)


class ManagerOverrideBody(BaseModel):
    user_id: int
    manager_user_id: int


class ExecStatusBody(BaseModel):
    user_id: int
    is_exec: bool


@router.get("/admin/people/search")
def admin_people_search(
    q: str = Query("", min_length=0, max_length=100),
    user: CurrentUser = Depends(get_current_user),
):
    """Company-wide people search for the admin page pickers (admins only)."""
    _require_admin(user)
    if not q or not q.strip():
        return {"employees": []}
    from routers.manager import _search_company_wide
    try:
        rows = _search_company_wide(q.strip().lower())
    except Exception as exc:
        logger.warning("admin people search failed: %s", exc)
        return {"employees": []}
    return {"employees": [
        {"user_id": r["user_id"], "name": r["name"],
         "department": r.get("department", ""), "designation": r.get("designation", "")}
        for r in rows[:20]
    ]}


@router.get("/admin/overrides")
def admin_get_overrides(user: CurrentUser = Depends(get_current_user)):
    """Current admin overrides, enriched with display names for the UI."""
    _require_admin(user)
    mgr = get_manager_overrides()
    exe = get_exec_overrides()
    return {
        "manager_overrides": [
            {"user_id": uid, "name": _display_name(uid),
             "manager_user_id": mid, "manager_name": _display_name(mid)}
            for uid, mid in mgr.items()
        ],
        "exec_overrides": [
            {"user_id": uid, "name": _display_name(uid), "is_exec": flag}
            for uid, flag in exe.items()
        ],
    }


@router.post("/admin/manager-override")
def admin_set_manager_override(
    body: ManagerOverrideBody, user: CurrentUser = Depends(get_current_user)
):
    """Reassign an employee to a different manager (overrides Classmate)."""
    _require_admin(user)
    if body.user_id == body.manager_user_id:
        raise HTTPException(status_code=400, detail="An employee cannot be their own manager")
    if not get_user_by_id(None, body.user_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    if not get_user_by_id(None, body.manager_user_id):
        raise HTTPException(status_code=404, detail="Manager not found")
    set_manager_override(body.user_id, body.manager_user_id, user.classmate_user_id, _now())
    _clear_manager_caches()
    logger.info("admin %s set manager override: user %s -> manager %s",
                user.classmate_user_id, body.user_id, body.manager_user_id)
    return {"ok": True}


@router.post("/admin/exec-status")
def admin_set_exec_status(
    body: ExecStatusBody, user: CurrentUser = Depends(get_current_user)
):
    """Grant/revoke exec-manager status. Granting requires the target already be
    an effective (override-aware) manager, otherwise 400 so the UI can prompt the
    admin to assign them as a manager first."""
    _require_admin(user)
    if not get_user_by_id(None, body.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if body.is_exec and not is_manager(None, body.user_id):
        raise HTTPException(
            status_code=400,
            detail="User is not a manager yet — assign them as a manager first.",
        )
    set_exec_override(body.user_id, body.is_exec, user.classmate_user_id, _now())
    logger.info("admin %s set exec status: user %s -> %s",
                user.classmate_user_id, body.user_id, body.is_exec)
    return {"ok": True}


@router.post("/admin/reset-overrides")
def admin_reset_overrides(user: CurrentUser = Depends(get_current_user)):
    """Global reset: clear all manager allocation overrides. Exec-status grants
    are intentionally left untouched."""
    _require_admin(user)
    deleted = reset_manager_overrides()
    _clear_manager_caches()
    logger.info("admin %s reset manager overrides (%d cleared)",
                user.classmate_user_id, deleted)
    return {"ok": True, "cleared": deleted}
