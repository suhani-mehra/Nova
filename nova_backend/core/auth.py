"""
core/auth.py

Azure AD authentication for Nova.

Current state: STUB MODE
- When AZURE_TENANT_ID is "placeholder" (local dev), auth is bypassed
  and Pradeep Menon (user_id=5575) is loaded from warehouse as the dev user.
- When real Azure AD values are in .env, full JWT validation runs.

No code changes needed when you go live — just update .env with
real Azure AD credentials and it switches automatically.
"""

import logging
import httpx
from functools import lru_cache
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from core.config import settings

logger = logging.getLogger(__name__)

# ── Bearer token extractor ────────────────────────────────────────────────────
bearer_scheme = HTTPBearer(auto_error=False)


# ── The user object passed around inside the app ──────────────────────────────
@dataclass
class CurrentUser:
    classmate_user_id: Optional[int]
    name: str
    email: str
    role: str          # "employee" | "manager" | "both"
    azure_oid: Optional[str] = None


# ── JWKS key fetching (Azure AD public keys for token verification) ───────────
@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    url = settings.azure_jwks_uri
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def _get_signing_key(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token header",
        )

    jwks = _get_jwks()
    for key in jwks.get("keys", []):
        if key["kid"] == header.get("kid"):
            return key

    _get_jwks.cache_clear()
    jwks = _get_jwks()
    for key in jwks.get("keys", []):
        if key["kid"] == header.get("kid"):
            return key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unable to find matching signing key",
    )


# ── Full Azure AD token validation (runs post-deployment) ────────────────────
def _validate_azure_token(token: str) -> dict:
    signing_key = _get_signing_key(token)

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.azure_client_id,
            issuer=settings.azure_issuer,
        )
    except JWTError as e:
        logger.warning("Azure AD token validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token validation failed",
        )

    return claims


# ── Dev bypass user cache ─────────────────────────────────────────────────────
_dev_user_cache: Optional[CurrentUser] = None
_exec_dev_user_cache: dict = {}  # keyed by user_id, caches resolved exec dev users


async def _get_dev_user() -> CurrentUser:
    """
    Loads Pradeep Menon (user_id=5575) from Fabric for local development.
    Result is cached after the first successful fetch so subsequent requests
    don't re-query the DB. Falls back to hardcoded values if Fabric is unreachable.
    """
    global _dev_user_cache
    if _dev_user_cache is not None:
        return _dev_user_cache

    try:
        from core.database import query as warehouse_query

        user_rows = warehouse_query(
            """
            SELECT id, aduser_name, email_id, first_name, last_name
            FROM   dim_classmate_user
            WHERE  id          = ?
              AND  is_active   = 1
              AND  etl_isactive = 1
            """,
            (5575,),
        )
        if not user_rows:
            raise ValueError("User 5575 not found in dim_classmate_user")

        u = user_rows[0]

        profile_rows = warehouse_query(
            """
            WITH latest_profiles AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                         PARTITION BY user_id
                         ORDER BY modified_on DESC
                       ) AS rn
                FROM dim_classmate_employee_profile
                WHERE etl_isactive = 1
                  AND is_active    = 1
                  AND is_deleted   = 0
                  AND country_code IS NOT NULL
                  AND UPPER(TRIM(country_code)) != 'OT'
            )
            SELECT LOWER(TRIM(display_name)) AS name
            FROM   latest_profiles
            WHERE  rn      = 1
              AND  user_id = ?
            """,
            (5575,),
        )

        if profile_rows and profile_rows[0]["name"]:
            display_name = profile_rows[0]["name"].title()
        else:
            fn = (u.get("first_name") or "").strip()
            ln = (u.get("last_name") or "").strip()
            display_name = f"{fn} {ln}".strip().title() or "Pradeep Menon"

        mgr_rows = warehouse_query(
            """
            WITH latest_profiles AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                         PARTITION BY user_id
                         ORDER BY modified_on DESC
                       ) AS rn
                FROM dim_classmate_employee_profile
                WHERE etl_isactive = 1
                  AND is_active    = 1
                  AND is_deleted   = 0
                  AND country_code IS NOT NULL
                  AND UPPER(TRIM(country_code)) != 'OT'
            )
            SELECT COUNT(*) AS report_count
            FROM   latest_profiles
            WHERE  rn      = 1
              AND  manager = ?
            """,
            (5575,),
        )

        report_count = int((mgr_rows[0]["report_count"] or 0)) if mgr_rows else 0
        role = "both" if report_count > 0 else "employee"

        _dev_user_cache = CurrentUser(
            classmate_user_id=5575,
            name=display_name,
            email=u.get("email_id") or "pradeep.menon@orioninc.com",
            role=role,
            azure_oid=u.get("aduser_name"),
        )
        logger.info("Dev user loaded from warehouse: %s (%s)", display_name, role)

    except Exception as exc:
        logger.warning("Warehouse lookup failed for dev user, using fallback: %s", exc)
        _dev_user_cache = CurrentUser(
            classmate_user_id=5575,
            name="Pradeep Menon",
            email="pradeep.menon@orioninc.com",
            role="both",
            azure_oid=None,
        )

    return _dev_user_cache


# ── Main dependency — import this in every protected route ────────────────────
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    """
    FastAPI dependency. Use like this in any router:

        @router.get("/api/something")
        def my_route(user: CurrentUser = Depends(get_current_user)):
            ...

    Behaviour:
    - Dev mode (AZURE_TENANT_ID=placeholder): returns Pradeep Menon from Fabric
    - Production (real Azure values in .env): validates Bearer token against Azure AD
    """

    # ── DEV BYPASS ────────────────────────────────────────────────────────────
    if not settings.azure_configured:
        from core.queries import get_user_by_id, is_manager

        EXEC_DEV_USER_IDS = {16467, 16465, 16470}  # Niva Shah, Eric Verdes, Suhani Mehra

        # Step 1: resolve the signed-in dev user (cached — avoids a Fabric hit per request)
        dev_user_hdr = request.headers.get("X-Nova-Dev-User")
        if dev_user_hdr:
            try:
                dev_uid = int(dev_user_hdr)
                if dev_uid in _exec_dev_user_cache:
                    signed_in_user = _exec_dev_user_cache[dev_uid]
                else:
                    dev_rows = get_user_by_id(None, dev_uid)
                    if dev_rows:
                        d = dev_rows[0]
                        d_name = (d.get("first_name", "") + " " + d.get("last_name", "")).strip().title()
                        d_role = "both" if is_manager(None, dev_uid) else "employee"
                        signed_in_user = CurrentUser(
                            classmate_user_id=dev_uid,
                            name=d_name,
                            email=d.get("email_id", ""),
                            role=d_role,
                            azure_oid=None,
                        )
                        _exec_dev_user_cache[dev_uid] = signed_in_user
                        logger.info("Dev sign-in: user %s (%s)", dev_uid, d_name)
                    else:
                        signed_in_user = await _get_dev_user()
            except (ValueError, Exception) as e:
                logger.warning("Dev user header failed: %s", e)
                signed_in_user = await _get_dev_user()
        else:
            signed_in_user = await _get_dev_user()

        # Step 2: check for impersonation (only for exec dev users)
        impersonate_hdr = request.headers.get("X-Nova-Impersonate")
        if impersonate_hdr and signed_in_user.classmate_user_id in EXEC_DEV_USER_IDS:
            try:
                target_id = int(impersonate_hdr)
                target_rows = get_user_by_id(None, target_id)
                if target_rows:
                    t = target_rows[0]
                    t_name = (t.get("first_name", "") + " " + t.get("last_name", "")).strip().title()
                    target_role = "both" if is_manager(None, target_id) else "employee"
                    logger.info("Dev impersonation: user %s as user %s (%s)",
                                signed_in_user.classmate_user_id, target_id, t_name)
                    return CurrentUser(
                        classmate_user_id=target_id,
                        name=t_name,
                        email=t.get("email_id", ""),
                        role=target_role,
                        azure_oid=None,
                    )
            except (ValueError, Exception) as e:
                logger.warning("Dev impersonation failed for %s: %s", impersonate_hdr, e)

        return signed_in_user

    # ── PRODUCTION: require a real Bearer token ───────────────────────────────
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    claims = _validate_azure_token(token)

    email = claims.get("preferred_username") or claims.get("upn") or ""
    name = claims.get("name") or email
    oid = claims.get("oid")

    from core.queries import get_user_by_adname, is_manager

    user_rows = get_user_by_adname(None, email)
    if not user_rows:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found in Classmate — contact your administrator",
        )
    u = user_rows[0]
    classmate_user_id = int(u["id"])
    user_role = "both" if is_manager(None, classmate_user_id) else "employee"

    # Executive dev mode — allow specific user IDs to impersonate others for testing
    EXEC_DEV_USER_IDS = {16467, 16465, 16470}  # Niva Shah, Eric Verdes, Suhani Mehra
    impersonate_hdr = request.headers.get("X-Nova-Impersonate")
    if impersonate_hdr and classmate_user_id in EXEC_DEV_USER_IDS:
        try:
            target_id = int(impersonate_hdr)
            from core.queries import get_user_by_id
            target_rows = get_user_by_id(None, target_id)
            if target_rows:
                t = target_rows[0]
                t_name = (t.get("first_name", "") + " " + t.get("last_name", "")).strip().title()
                target_role = "both" if is_manager(None, target_id) else "employee"
                return CurrentUser(
                    classmate_user_id=target_id,
                    name=t_name,
                    email=t.get("email_id", ""),
                    role=target_role,
                    azure_oid=oid,
                )
        except (ValueError, Exception):
            pass

    return CurrentUser(
        classmate_user_id=classmate_user_id,
        name=name,
        email=email,
        role=user_role,
        azure_oid=oid,
    )
