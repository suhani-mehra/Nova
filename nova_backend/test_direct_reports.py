"""
test_direct_reports.py
Run from nova_backend/:
    cd nova_backend && python3 test_direct_reports.py

Connects to Fabric and prints every row in dim_classmate_employee_profile
where manager = 5575 (Pradeep Menon), with no dedup CTE or extra filters —
just the raw table so we can see exactly what's there.
"""

import struct
import sys
import os

# ── Load .env ─────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(".env")

FABRIC_SERVER   = os.environ["FABRIC_SERVER"]
FABRIC_DATABASE = os.environ["FABRIC_DATABASE"]
FABRIC_DRIVER   = os.environ.get("FABRIC_DRIVER", "/opt/homebrew/lib/libmsodbcsql.18.dylib")
AUTH_TENANT_ID  = os.environ.get("FABRIC_AUTH_TENANT_ID") or os.environ.get("AZURE_TENANT_ID")

import pyodbc
from azure.identity import InteractiveBrowserCredential

print(f"\nConnecting to:  {FABRIC_SERVER}")
print(f"Database:       {FABRIC_DATABASE}")
print(f"Auth tenant:    {AUTH_TENANT_ID}")
print("\nA browser window will open for login...\n")

cred = InteractiveBrowserCredential(tenant_id=AUTH_TENANT_ID)
token = cred.get_token("https://database.windows.net/.default")
token_bytes = token.token.encode("utf-16-le")
token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

conn_str = (
    f"Driver={{{FABRIC_DRIVER}}};"
    f"Server={FABRIC_SERVER},1433;"
    f"Database={FABRIC_DATABASE};"
    "Encrypt=yes;"
    "TrustServerCertificate=no;"
    "Connection Timeout=30;"
)

conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
print("✓ Connected\n")

# ── Query 1: raw rows where manager = 5575 (no filters) ──────────────────────
print("=" * 60)
print("Query 1: raw rows with manager = 5575 (no other filters)")
print("=" * 60)
cursor = conn.cursor()
cursor.execute("""
    SELECT TOP 30
        user_id,
        employee_id,
        display_name,
        department_code,
        is_active,
        is_deleted,
        etl_isactive
    FROM classmate.dim_classmate_employee_profile
    WHERE manager = 5575
    ORDER BY display_name
""")
rows = cursor.fetchall()
cols = [d[0] for d in cursor.description]

if not rows:
    print("  *** NO ROWS FOUND with manager = 5575 ***")
    print("  → Pradeep may not be stored as manager by user_id (5575).")
    print("  → Try Query 2 below to check what value is used.\n")
else:
    print(f"  Found {len(rows)} row(s):\n")
    header = "  " + "  ".join(f"{c:<20}" for c in cols)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in rows:
        print("  " + "  ".join(f"{str(v):<20}" for v in row))

# ── Query 2: look up what employee_id or user_id Pradeep has ─────────────────
print("\n" + "=" * 60)
print("Query 2: Pradeep's own profile rows (user_id = 5575)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 5
        user_id,
        employee_id,
        display_name,
        manager,
        is_active,
        is_deleted,
        etl_isactive
    FROM classmate.dim_classmate_employee_profile
    WHERE user_id = 5575
    ORDER BY modified_on DESC
""")
rows2 = cursor.fetchall()
cols2 = [d[0] for d in cursor.description]

if not rows2:
    print("  *** NO ROWS with user_id = 5575 ***")
else:
    print(f"  Found {len(rows2)} row(s) — 'manager' column = the person Pradeep reports TO:\n")
    header2 = "  " + "  ".join(f"{c:<20}" for c in cols2)
    print(header2)
    print("  " + "-" * (len(header2) - 2))
    for row in rows2:
        print("  " + "  ".join(f"{str(v):<20}" for v in row))

# ── Query 3: look up by email to find Pradeep's real user_id ─────────────────
print("\n" + "=" * 60)
print("Query 3: find Pradeep by email in dim_classmate_user")
print("=" * 60)
cursor.execute("""
    SELECT TOP 5
        u.id        AS user_id,
        u.aduser_name,
        u.email_id,
        u.is_active
    FROM classmate.dim_classmate_user u
    WHERE LOWER(u.email_id) LIKE '%pradeep%'
       OR LOWER(u.aduser_name) LIKE '%pradeep%'
""")
rows3 = cursor.fetchall()
cols3 = [d[0] for d in cursor.description]

if not rows3:
    print("  *** No user found matching 'pradeep' ***")
else:
    print(f"  Found {len(rows3)} match(es):\n")
    header3 = "  " + "  ".join(f"{c:<25}" for c in cols3)
    print(header3)
    print("  " + "-" * (len(header3) - 2))
    for row in rows3:
        print("  " + "  ".join(f"{str(v):<25}" for v in row))

# ── Query 4: if no results above, check what values appear in 'manager' col ──
if not rows:
    print("\n" + "=" * 60)
    print("Query 4: sample of distinct manager values in the table")
    print("(to see what format manager IDs are stored as)")
    print("=" * 60)
    cursor.execute("""
        SELECT TOP 20 manager, COUNT(*) AS cnt
        FROM classmate.dim_classmate_employee_profile
        WHERE is_active = 1 AND is_deleted = 0
        GROUP BY manager
        ORDER BY cnt DESC
    """)
    rows4 = cursor.fetchall()
    if rows4:
        print(f"  Top manager values (by headcount):\n")
        for row in rows4:
            print(f"    manager={row[0]}  →  {row[1]} direct report(s)")
    else:
        print("  (no rows)")

conn.close()
print("\n✓ Done\n")
