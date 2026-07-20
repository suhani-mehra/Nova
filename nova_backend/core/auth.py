"""
core/auth.py

Azure AD authentication for Nova.

Current state: STUB MODE
- When AZURE_TENANT_ID is "placeholder" (local dev), auth is bypassed
  and the local dev user (settings.dev_fallback_user_id) is loaded from
  warehouse as the signed-in user.
- When real Azure AD values are in .env, full JWT validation runs.

No code changes needed when you go live — just update .env with
real Azure AD credentials and it switches automatically.
"""

import logging
import httpx
from functools import lru_cache
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
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
        # Strip CR/LF from the warehouse-sourced name before logging (CWE-117
        # defense-in-depth); does not alter the stored CurrentUser.name.
        logger.info("Dev user loaded from warehouse: %s (%s)",
                    display_name.replace("\r", " ").replace("\n", " "), role)

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
    """Resolve Azure AD claims -> Classmate user row -> CurrentUser.
    Raises 401 if the AD email isn't found in Classmate."""
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
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    """
    FastAPI dependency. Use like this in any router:

        @router.get("/api/something")
        def my_route(user: CurrentUser = Depends(get_current_user)):
            ...

    Behaviour:
    - Dev mode (AZURE_TENANT_ID=placeholder): returns the local dev user from Fabric
    - Production (real Azure values in .env): validates Bearer token against Azure AD
    """

    # ── DEV BYPASS ────────────────────────────────────────────────────────────
    if not settings.azure_configured:
        return await _get_dev_user()

    # ── PRODUCTION: require a real Bearer token ───────────────────────────────
    claims = _validate_bearer(credentials)
    return _build_prod_user(claims)
