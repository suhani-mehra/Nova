"""
core/database.py
Microsoft Fabric Data Warehouse connection via pyodbc.

Uses ActiveDirectoryInteractive auth — the ODBC driver handles the browser
login and token acquisition natively, which is the most reliable method for
Fabric Data Warehouse.

Usage:
    from core.database import get_connection, query, query_df
"""

import logging
import struct
import threading
import time
from typing import Optional

import pandas as pd
import pyodbc
from azure.identity import InteractiveBrowserCredential

from core.config import settings

logger = logging.getLogger(__name__)

# ── Connection cache ──────────────────────────────────────────────────────────
# pyodbc connections are NOT safe for concurrent use across threads.
# We keep one credential (shared — one browser login) but give each worker
# thread its own connection via threading.local().

_TOKEN_SCOPE = "https://database.windows.net/.default"
_CONNECTION_TTL_SECONDS = 55 * 60  # reconnect before the 60-min Azure token expiry

_credential: Optional[InteractiveBrowserCredential] = None
_credential_lock = threading.Lock()
_local = threading.local()   # per-thread: .connection, .connected_at


def _is_stale(connected_at: float) -> bool:
    return (time.monotonic() - connected_at) >= _CONNECTION_TTL_SECONDS


def _get_credential() -> InteractiveBrowserCredential:
    global _credential
    with _credential_lock:
        if _credential is None:
            # For B2B guest accounts the token must come from the user's HOME tenant,
            # not the Orion (resource) tenant.  Set FABRIC_AUTH_TENANT_ID in .env to
            # override; falls back to AZURE_TENANT_ID for native Orion accounts.
            auth_tenant = settings.fabric_auth_tenant_id or settings.azure_tenant_id
            _credential = InteractiveBrowserCredential(tenant_id=auth_tenant)
    return _credential


def _open_connection() -> pyodbc.Connection:
    """
    Gets an Azure token via browser login (once per process) and passes it
    to pyodbc via SQL_COPT_SS_ACCESS_TOKEN (attrs_before key 1256).
    """
    credential = _get_credential()
    token = credential.get_token(_TOKEN_SCOPE)
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn_str = (
        f"Driver={{{settings.fabric_driver}}};"
        f"Server={settings.fabric_server};"
        f"Database={settings.fabric_database};"
    )
    return pyodbc.connect(conn_str, attrs_before={1256: token_struct})


def get_connection() -> pyodbc.Connection:
    """
    Returns an active pyodbc connection for the current thread.
    - First call on any thread: reuses the cached credential (one browser login
      for the whole process), opens a fresh pyodbc connection for this thread.
    - Subsequent calls on the same thread within 55 min: return the cached
      per-thread connection with no round-trip to Azure.
    - After 55 min: silently reconnects this thread's connection.
    """
    conn = getattr(_local, "connection", None)
    connected_at = getattr(_local, "connected_at", 0.0)

    if conn is not None and not _is_stale(connected_at):
        return conn

    if conn is not None:
        logger.info("Fabric connection stale (thread %s) — reconnecting.", threading.current_thread().name)
        try:
            conn.close()
        except Exception:
            pass
        _local.connection = None

    try:
        _local.connection = _open_connection()
        _local.connected_at = time.monotonic()
        logger.info("Fabric connection established (thread %s).", threading.current_thread().name)
    except Exception as exc:
        _local.connection = None
        if settings.is_dev:
            logger.error("Fabric connection failed (dev mode): %s", exc)
        raise

    return _local.connection


# ── Public query helpers ──────────────────────────────────────────────────────

def query(sql: str, params: Optional[tuple] = None) -> list[dict]:
    """
    Executes a parameterised SQL query and returns results as a list of dicts.

    Example:
        rows = query("SELECT * FROM dim_user WHERE id = ?", (user_id,))
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        if settings.is_dev:
            logger.error("Query failed: %s\nSQL: %s\nParams: %s", exc, sql, params)
        raise


def query_df(sql: str, params: Optional[tuple] = None) -> pd.DataFrame:
    """
    Executes a parameterised SQL query and returns results as a pandas DataFrame.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        return pd.DataFrame([list(row) for row in rows], columns=columns)
    except Exception as exc:
        if settings.is_dev:
            logger.error("query_df failed: %s\nSQL: %s\nParams: %s", exc, sql, params)
        raise
