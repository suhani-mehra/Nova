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
import time
from typing import Optional

import pandas as pd
import pyodbc
from azure.identity import InteractiveBrowserCredential

from core.config import settings

logger = logging.getLogger(__name__)

# ── Connection cache ──────────────────────────────────────────────────────────

_TOKEN_SCOPE = "https://database.windows.net/.default"
_CONNECTION_TTL_SECONDS = 55 * 60  # reconnect before the 60-min Azure token expiry

_credential: Optional[InteractiveBrowserCredential] = None
_connection: Optional[pyodbc.Connection] = None
_connected_at: float = 0.0


def _is_connection_stale() -> bool:
    return (time.monotonic() - _connected_at) >= _CONNECTION_TTL_SECONDS


def _get_credential() -> InteractiveBrowserCredential:
    global _credential
    if _credential is None:
        # No tenant_id — using the common endpoint produces a token with the
        # upn claim that Fabric requires for SQL auth. Specifying tenant_id
        # omits upn and causes 18456.
        _credential = InteractiveBrowserCredential()
    return _credential


def _open_connection() -> pyodbc.Connection:
    """
    Gets an Azure token via browser login (once per session) and passes it
    to pyodbc via SQL_COPT_SS_ACCESS_TOKEN (attrs_before key 1256).

    # TODO (SSO phase): swap InteractiveBrowserCredential for the user's
    # delegated credential from get_current_user() so the connection runs
    # as the logged-in user rather than a shared interactive session.
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
    Returns an active pyodbc connection to Fabric.
    - First call: opens browser login.
    - Subsequent calls within 55 min: returns cached connection, no re-prompt.
    - After 55 min: reconnects silently using the cached credential.
    """
    global _connection, _connected_at

    if _connection is not None and not _is_connection_stale():
        return _connection

    if _connection is not None:
        logger.info("Fabric connection is stale — reconnecting.")
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None

    try:
        _connection = _open_connection()
        _connected_at = time.monotonic()
        logger.info("Fabric connection established.")
    except Exception as exc:
        _connection = None
        if settings.is_dev:
            logger.error("Fabric connection failed (dev mode): %s", exc)
        raise

    return _connection


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
