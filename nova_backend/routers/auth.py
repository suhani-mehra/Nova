"""
routers/auth.py
Dev-only endpoint to verify Azure AD App Registration connectivity.

GET /auth/token
  Uses ClientSecretCredential (client_id + client_secret + tenant_id) to
  acquire an application token from Azure AD and returns the decoded claims.
  Confirms that the credentials in .env are correct and the tenant is reachable.

  Returns 400 when Azure is not configured (AZURE_TENANT_ID=placeholder).
  Returns 404 in production (NOVA_ENV != development).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from azure.identity import ClientSecretCredential
from jose import jwt as jose_jwt

from core.auth import CurrentUser, get_current_user
from core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/auth/token")
def test_azure_connection():
    """
    Verifies Azure AD App Registration credentials by performing a client
    credentials token acquisition.  Safe to call without a Bearer token —
    it uses the app's own identity, not the caller's.

    Only available when NOVA_ENV=development.
    """
    if not settings.is_dev:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not settings.azure_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Azure AD is not configured. "
                "Set real values for AZURE_TENANT_ID, AZURE_CLIENT_ID, and "
                "AZURE_CLIENT_SECRET in .env, then restart the server."
            ),
        )

    try:
        credential = ClientSecretCredential(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret,
        )
        # Client credentials flow: acquires a token for the app itself.
        # Scope uses .default to request whatever permissions the app registration has.
        token_response = credential.get_token("https://graph.microsoft.com/.default")
    except Exception as exc:
        logger.error("Azure AD credential acquisition failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Azure AD credential acquisition failed",
        )

    # Decode claims without re-verifying (token already came from Azure AD).
    try:
        claims = jose_jwt.get_unverified_claims(token_response.token)
    except Exception as exc:
        logger.warning("test_azure_connection: could not decode token claims: %s", exc)
        claims = {}

    return {
        "status": "ok",
        "azure_tenant_id": claims.get("tid"),
        "app_id": claims.get("appid") or claims.get("azp"),
        "issuer": claims.get("iss"),
        "expires_at": claims.get("exp"),
        "token_type": claims.get("idtyp", "app"),
        # Show which JWKS URI will be used when validating incoming user tokens.
        "jwks_uri": settings.azure_jwks_uri,
        "expected_issuer": settings.azure_issuer,
    }


@router.get("/auth/me")
def whoami(user: CurrentUser = Depends(get_current_user)):
    """
    Returns the resolved identity for the current request.
    Useful for confirming the JWT validation pipeline is working end-to-end:
      curl -H "Authorization: Bearer <token>" http://localhost:8000/auth/me
    """
    return {
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "azure_oid": user.azure_oid,
        "classmate_user_id": user.classmate_user_id,
        "dev_bypass": not settings.azure_configured,
    }
