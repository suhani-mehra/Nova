"""
Quick standalone test — run this directly to verify Fabric connectivity
outside of uvicorn/async context.

    python test_fabric.py
"""
import struct
import pyodbc
from azure.identity import InteractiveBrowserCredential, DeviceCodeCredential
from core.config import settings

print(f"Server:   {settings.fabric_server}")
print(f"Database: {settings.fabric_database}")
print(f"Driver:   {settings.fabric_driver}")
print()

import base64, json

def decode_jwt(token_str):
    """Decode JWT payload without verifying signature."""
    payload = token_str.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)  # pad base64
    return json.loads(base64.urlsafe_b64decode(payload))

# ── Attempt 1: manual token via azure-identity ────────────────────────────────
print("--- Attempt 1: manual token (InteractiveBrowserCredential) ---")
try:
    # login_hint forces the Orion work account and bypasses any cached personal account
    cred = InteractiveBrowserCredential(
        tenant_id=settings.azure_tenant_id,
        login_hint="suhani.mehra@orioninc.com",
    )
    token = cred.get_token("https://database.windows.net/.default")
    print(f"Token acquired. Length: {len(token.token)} chars")

    claims = decode_jwt(token.token)
    print(f"  aud              : {claims.get('aud')}")
    print(f"  upn              : {claims.get('upn')}")
    print(f"  preferred_username: {claims.get('preferred_username')}")
    print(f"  unique_name      : {claims.get('unique_name')}")
    print(f"  name             : {claims.get('name')}")
    print(f"  email            : {claims.get('email')}")
    print(f"  oid              : {claims.get('oid')}")
    print(f"  tid              : {claims.get('tid')}")
    print(f"  scp              : {claims.get('scp')}")
    print(f"  idtyp            : {claims.get('idtyp')}")

    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn_str = (
        f"Driver={{{settings.fabric_driver}}};"
        f"Server={settings.fabric_server},1433;"
        f"Database={settings.fabric_database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
    row = conn.cursor().execute("SELECT 1").fetchone()
    print(f"SUCCESS via manual token. Result: {row[0]}")
    conn.close()
except Exception as e:
    print(f"FAILED: {e}")

print()

# ── Attempt 2: ActiveDirectoryInteractive in connection string ────────────────
print("--- Attempt 2: ActiveDirectoryInteractive (browser handled by ODBC driver) ---")
print("(A browser window should open — log in within 5 minutes)")
try:
    conn_str = (
        f"Driver={{{settings.fabric_driver}}};"
        f"Server={settings.fabric_server},1433;"
        f"Database={settings.fabric_database};"
        "Authentication=ActiveDirectoryInteractive;"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=300;"
    )
    conn = pyodbc.connect(conn_str)
    row = conn.cursor().execute("SELECT 1").fetchone()
    print(f"SUCCESS via ActiveDirectoryInteractive. Result: {row[0]}")
    conn.close()
except Exception as e:
    print(f"FAILED: {e}")

print()

# ── Attempt 3: Service principal auth ────────────────────────────────────────
print("--- Attempt 3: Service principal (client credentials) ---")
try:
    conn_str = (
        f"Driver={{{settings.fabric_driver}}};"
        f"Server={settings.fabric_server},1433;"
        f"Database={settings.fabric_database};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={settings.azure_client_id};"
        f"PWD={settings.azure_client_secret};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    conn = pyodbc.connect(conn_str)
    row = conn.cursor().execute("SELECT 1").fetchone()
    print(f"SUCCESS via service principal. Result: {row[0]}")
    conn.close()
except Exception as e:
    print(f"FAILED: {e}")

print()

# ── Attempt 4: DeviceCodeCredential ──────────────────────────────────────────
print("--- Attempt 4: DeviceCodeCredential ---")
print("(Go to aka.ms/devicelogin in your browser and enter the displayed code)")
try:
    cred = DeviceCodeCredential(tenant_id=settings.azure_tenant_id)
    token = cred.get_token("https://database.windows.net/.default")
    print(f"Token acquired. Length: {len(token.token)} chars")

    claims = decode_jwt(token.token)
    print(f"  upn              : {claims.get('upn')}")
    print(f"  preferred_username: {claims.get('preferred_username')}")
    print(f"  unique_name      : {claims.get('unique_name')}")

    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn_str = (
        f"Driver={{{settings.fabric_driver}}};"
        f"Server={settings.fabric_server},1433;"
        f"Database={settings.fabric_database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
    row = conn.cursor().execute("SELECT 1").fetchone()
    print(f"SUCCESS via DeviceCodeCredential. Result: {row[0]}")
    conn.close()
except Exception as e:
    print(f"FAILED: {e}")
