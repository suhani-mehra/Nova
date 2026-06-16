"""
core/auth.py

Azure AD authentication for Nova.

Current state: STUB MODE
- When AZURE_TENANT_ID is "placeholder" (local dev), auth is bypassed
  and a dummy user is returned so you can develop without AD.
- When real Azure AD values are in .env, full JWT validation runs.

No code changes needed when you go live — just update .env with
real Azure AD credentials and it switches automatically.
"""

import httpx
from functools import lru_cache
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from core.config import settings


# ── Bearer token extractor ────────────────────────────────────────────────────
# This tells FastAPI to look for "Authorization: Bearer <token>" on requests.
# auto_error=False means we handle the missing-token case ourselves below.
bearer_scheme = HTTPBearer(auto_error=False)


# ── The user object passed around inside the app ──────────────────────────────
@dataclass
class CurrentUser:
    classmate_user_id: Optional[int]  # None until CSV loader is wired up
    name: str
    email: str
    role: str                          # "employee" or "manager"
    azure_oid: Optional[str] = None    # Azure AD object ID (available post-AD)


# ── JWKS key fetching (Azure AD public keys for token verification) ───────────
@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """
    Fetches Azure AD's public signing keys.
    Cached in memory — refreshed automatically if a key ID (kid) is missing.
    Only runs when Azure AD values are real (not placeholder).
    """
    url = settings.azure_jwks_uri
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def _get_signing_key(token: str) -> dict:
    """
    Extracts the correct public key from the JWKS endpoint
    matching the kid (key ID) in the token header.
    """
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

    # kid not found — keys may have rotated, clear cache and retry once
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
    """
    Validates the JWT against Azure AD:
    - Signature verified using Azure's public keys
    - Issuer matches our tenant
    - Audience matches our app client ID
    - Token is not expired

    Returns the decoded token claims dict.
    """
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}",
        )

    return claims


# ── Dev bypass user (used when Azure AD is not yet configured) ────────────────
def _get_dev_user() -> CurrentUser:
    """
    Returns a hardcoded user for local development.
    This is only used when AZURE_TENANT_ID=placeholder in .env.
    Swap 'role' between "employee" and "manager" to test both views.
    """
    return CurrentUser(
        classmate_user_id=None,   # will be a real ID once DB is wired
        name="Dev User",
        email="dev.user@orioninc.com",
        role="employee",           # change to "manager" to test manager views
        azure_oid="dev-placeholder-oid",
    )


# ── Role detection (runs once DB is connected) ────────────────────────────────
def _detect_role(classmate_user_id: int, db) -> str:
    """
    A user is a manager if their user_id appears in the
    dim_employee_profile.manager column for any other employee.

    db: SQLAlchemy connection or cursor — wired up in Phase 2
    when the CSV loader is complete.
    """
    # TODO: uncomment when database is connected in Phase 2
    # result = db.execute(
    #     "SELECT 1 FROM dim_employee_profile WHERE manager = ? LIMIT 1",
    #     (classmate_user_id,)
    # ).fetchone()
    # return "manager" if result else "employee"
    return "employee"  # default until DB is live


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
    - Dev mode (AZURE_TENANT_ID=placeholder): returns dummy user, no token needed
    - Production (real Azure values in .env): validates Bearer token against Azure AD
    """

    # ── DEV BYPASS ────────────────────────────────────────────────────────────
    # Bypass only when Azure credentials are genuinely absent (AZURE_TENANT_ID=placeholder).
    # When real credentials are present, enforce JWT validation even in local dev.
    # To run the frontend without tokens during UI work, set AZURE_TENANT_ID=placeholder.
    if not settings.azure_configured:
        return _get_dev_user()

    # ── PRODUCTION: require a real Bearer token ───────────────────────────────
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    claims = _validate_azure_token(token)

    # Extract user identity from token claims
    # Azure AD puts the UPN (email) in 'preferred_username' or 'upn'
    email = claims.get("preferred_username") or claims.get("upn") or ""
    name = claims.get("name") or email
    oid = claims.get("oid")

    # TODO Phase 2: look up classmate_user_id from dim_user table
    # For now we carry the Azure identity and wire the DB lookup later:
    # user_row = db.execute(
    #     "SELECT id FROM dim_user WHERE aduser_name = ? OR email_id = ?",
    #     (email, email)
    # ).fetchone()
    # if not user_row:
    #     raise HTTPException(status_code=401, detail="User not found in Classmate")
    # classmate_user_id = user_row[0]

    return CurrentUser(
        classmate_user_id=None,   # replace with classmate_user_id above in Phase 2
        name=name,
        email=email,
        role="employee",          # replace with _detect_role() in Phase 2
        azure_oid=oid,
    )
