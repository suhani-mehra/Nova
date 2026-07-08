"""
core/api_client.py
Client for the Classmate table-dump API (APIM → Boomi → lakehouse).

The API is not a query API: it accepts only {tableName, pageNumber, pageSize}
and returns one page of a whole table. Rows live at response["result"]["rows"];
every row carries TotalCount / TotalPages for pagination.

Auth requires BOTH:
  - the APIM subscription key header (Ocp-Apim-Subscription-Key), and
  - an OAuth Bearer token from the client-credentials flow.

Usage:
    from core.api_client import fetch_table
    for page_rows in fetch_table("dim_classmate_user"):
        ...  # list[dict] per page, TotalCount/TotalPages stripped
"""

import logging
import threading
import time
from typing import Iterator, Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_TOKEN_REFRESH_MARGIN = 60  # refresh this many seconds before expiry
_MAX_RETRIES = 3
_PAGINATION_COLS = ("TotalCount", "TotalPages")

_token_lock = threading.Lock()
_token: Optional[str] = None
_token_expires_at: float = 0.0


def _get_token() -> str:
    """Client-credentials token, cached until ~60s before expiry."""
    global _token, _token_expires_at
    with _token_lock:
        if _token and time.monotonic() < _token_expires_at:
            return _token
        resp = httpx.post(
            _TOKEN_URL.format(tenant=settings.api_tenant_id),
            data={
                "grant_type": "client_credentials",
                "client_id": settings.api_client_id,
                "client_secret": settings.api_client_secret,
                "scope": settings.api_scope,
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        _token = payload["access_token"]
        _token_expires_at = (
            time.monotonic() + int(payload.get("expires_in", 3599)) - _TOKEN_REFRESH_MARGIN
        )
        logger.info("Classmate API token acquired (expires_in=%s)", payload.get("expires_in"))
        return _token


def _fetch_page(client: httpx.Client, table_name: str, page_number: int, page_size: int) -> dict:
    """POST one page; retries transient failures and 401s (token refresh)."""
    global _token
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.post(
                settings.api_endpoint_url,
                headers={
                    "Ocp-Apim-Subscription-Key": settings.api_subscription_key,
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {_get_token()}",
                },
                json={
                    "tableName": table_name,
                    "pageNumber": page_number,
                    "pageSize": page_size,
                },
                timeout=120,
            )
            if resp.status_code == 401:
                # Token likely expired mid-run — drop cache and retry.
                with _token_lock:
                    _token = None
                raise httpx.HTTPStatusError("401 from API", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "API fetch %s page %d failed (attempt %d/%d), retrying in %ds: %s",
                    table_name, page_number, attempt + 1, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)
    raise RuntimeError(f"API fetch failed for {table_name} page {page_number}") from last_exc


def fetch_table(table_name: str, page_size: Optional[int] = None) -> Iterator[list[dict]]:
    """
    Yields the full table one page at a time as list[dict], with the
    TotalCount/TotalPages pagination columns stripped from each row.
    """
    page_size = page_size or settings.api_page_size
    with httpx.Client() as client:
        page_number = 1
        total_pages = None
        while total_pages is None or page_number <= total_pages:
            data = _fetch_page(client, table_name, page_number, page_size)
            rows = (data.get("result") or {}).get("rows") or []
            if not rows:
                break  # defensive: empty page means we're done regardless of TotalPages
            if total_pages is None:
                total_pages = int(rows[0].get("TotalPages") or 1)
                logger.info(
                    "Syncing %s: TotalCount=%s, TotalPages=%d (page_size=%d)",
                    table_name, rows[0].get("TotalCount"), total_pages, page_size,
                )
            yield [
                {k: v for k, v in row.items() if k not in _PAGINATION_COLS}
                for row in rows
            ]
            page_number += 1


def fetch_table_count(table_name: str) -> int:
    """TotalCount for a table via a 1-row probe (used for sync verification)."""
    with httpx.Client() as client:
        data = _fetch_page(client, table_name, 1, 1)
        rows = (data.get("result") or {}).get("rows") or []
        return int(rows[0]["TotalCount"]) if rows else 0
