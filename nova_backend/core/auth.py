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

# Executive dev users allowed to impersonate others for testing (dev-bypass
# and production paths both check against this same set). Sourced from .env
# (EXEC_DEV_USER_IDS) so no privileged IDs are hardcoded in scanned source.
EXEC_DEV_USER_IDS = set(settings.exec_dev_user_ids)

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

    dev_uid = settings.dev_fallback_user_id  # dev-only identity, sourced from .env

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
            (dev_uid,),
        )
        if not user_rows:
            raise ValueError(f"Dev user {dev_uid} not found in dim_classmate_user")

        dev_user_row = user_rows[0]

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
            (dev_uid,),
        )

        if profile_rows and profile_rows[0]["name"]:
            display_name = profile_rows[0]["name"].title()
        else:
            first_name = (dev_user_row.get("first_name") or "").strip()
            last_name = (dev_user_row.get("last_name") or "").strip()
            display_name = f"{first_name} {last_name}".strip().title() or "Dev User"

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
            (dev_uid,),
        )

        report_count = int((mgr_rows[0]["report_count"] or 0)) if mgr_rows else 0
        role = "both" if report_count > 0 else "employee"

        _dev_user_cache = CurrentUser(
            classmate_user_id=dev_uid,
            name=display_name,
            email=dev_user_row.get("email_id") or settings.dev_fallback_email,
            role=role,
            azure_oid=dev_user_row.get("aduser_name"),
        )
        logger.info("Dev user loaded from warehouse: %s (%s)", display_name, role)

    except Exception as exc:
        logger.warning("Warehouse lookup failed for dev user, using fallback: %s", exc)
        _dev_user_cache = CurrentUser(
            classmate_user_id=dev_uid,
            name=settings.dev_fallback_email.split("@")[0].replace(".", " ").title() or "Dev User",
            email=settings.dev_fallback_email,
            role="both",
            azure_oid=None,
        )

    return _dev_user_cache


async def _resolve_dev_signed_in_user(request: Request) -> CurrentUser:
    """Dev-bypass: resolve the signed-in user from the X-Nova-Dev-User header
    (cached per-uid so subsequent requests skip the Fabric hit), falling back
    to the hardcoded Fabric dev user (_get_dev_user) when the header is
    missing, unparseable, or the referenced user isn't found."""
    from core.queries import get_user_by_id, is_manager

    dev_user_hdr = request.headers.get("X-Nova-Dev-User")
    if not dev_user_hdr:
        return await _get_dev_user()

    try:
        dev_uid = int(dev_user_hdr)
        if dev_uid in _exec_dev_user_cache:
            return _exec_dev_user_cache[dev_uid]
        dev_rows = get_user_by_id(None, dev_uid)
        if not dev_rows:
            return await _get_dev_user()
        signed_in_row = dev_rows[0]
        signed_in_name = (signed_in_row.get("first_name", "") + " " + signed_in_row.get("last_name", "")).strip().title()
        signed_in_role = "both" if is_manager(None, dev_uid) else "employee"
        signed_in_user = CurrentUser(
            classmate_user_id=dev_uid,
            name=signed_in_name,
            email=signed_in_row.get("email_id", ""),
            role=signed_in_role,
            azure_oid=None,
        )
        _exec_dev_user_cache[dev_uid] = signed_in_user
        logger.info("Dev sign-in: user %s (%s)", dev_uid, signed_in_name)
        return signed_in_user
    except (ValueError, Exception) as e:
        logger.warning("Dev user header failed: %s", e)
        return await _get_dev_user()


def _resolve_impersonation_target(
    request: Request, actor_id: Optional[int], azure_oid: Optional[str] = None
) -> Optional[CurrentUser]:
    """Resolve an X-Nova-Impersonate header into a CurrentUser for the target
    user, gated on `actor_id` being an exec dev user. Shared by both the
    dev-bypass and production paths (azure_oid is None for dev, the real oid
    for production). Returns None — never raises — whenever there's no header,
    the actor isn't authorized, the target isn't found, or resolution fails;
    callers fall through to their own non-impersonated user in every case."""
    impersonate_hdr = request.headers.get("X-Nova-Impersonate")
    if not (impersonate_hdr and actor_id in EXEC_DEV_USER_IDS):
        return None

    from core.queries import get_user_by_id, is_manager
    try:
        target_id = int(impersonate_hdr)
        target_rows = get_user_by_id(None, target_id)
        if target_rows:
            target_row = target_rows[0]
            target_name = (target_row.get("first_name", "") + " " + target_row.get("last_name", "")).strip().title()
            target_role = "both" if is_manager(None, target_id) else "employee"
            logger.info("Impersonation: actor %s as user %s (%s)", actor_id, target_id, target_name)
            return CurrentUser(
                classmate_user_id=target_id,
                name=target_name,
                email=target_row.get("email_id", ""),
                role=target_role,
                azure_oid=azure_oid,
            )
    except (ValueError, Exception) as exc:
        logger.warning("Impersonation failed for %s: %s", impersonate_hdr, exc)
    return None


def _validate_bearer(credentials: Optional[HTTPAuthorizationCredentials]) -> dict:
    """401-guard + Azure JWT validation; returns the decoded claims dict."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _validate_azure_token(credentials.credentials)


def _build_prod_user(claims: dict) -> CurrentUser:
    """Resolve Azure AD claims -> Classmate user row -> CurrentUser
    (non-impersonated). Raises 401 if the AD email isn't found in Classmate."""
    from core.queries import get_user_by_adname, is_manager

    email = claims.get("preferred_username") or claims.get("upn") or ""
    name = claims.get("name") or email
    oid = claims.get("oid")

    user_rows = get_user_by_adname(None, email)
    if not user_rows:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found in Classmate — contact your administrator",
        )
    azure_user_row = user_rows[0]
    classmate_user_id = int(azure_user_row["id"])
    user_role = "both" if is_manager(None, classmate_user_id) else "employee"

    return CurrentUser(
        classmate_user_id=classmate_user_id,
        name=name,
        email=email,
        role=user_role,
        azure_oid=oid,
    )


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
        signed_in_user = await _resolve_dev_signed_in_user(request)
        target = _resolve_impersonation_target(request, signed_in_user.classmate_user_id, azure_oid=None)
        return target if target else signed_in_user

    # ── PRODUCTION: require a real Bearer token ───────────────────────────────
    claims = _validate_bearer(credentials)
    prod_user = _build_prod_user(claims)
    target = _resolve_impersonation_target(request, prod_user.classmate_user_id, azure_oid=prod_user.azure_oid)
    return target if target else prod_user
