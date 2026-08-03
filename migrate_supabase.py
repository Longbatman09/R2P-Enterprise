"""
migrate_supabase.py
===================
Run this from Pydroid to apply all Supabase schema fixes without
touching the SQL Editor manually.

What it does (in order):
  1. Adds service_role RLS bypass policy to profiles table
  2. Makes pipeline_runs.student_id NULLable
  3. Adds updated_at auto-update triggers

Usage in Pydroid:
  cd /storage/shared/Download/R2P   (or your actual path)
  python migrate_supabase.py
"""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ---- Load .env from project root ----
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    sys.exit(1)

# Remove /auth/v1 suffix if accidentally included
SUPABASE_URL = SUPABASE_URL.replace("/auth/v1", "").replace("/rest/v1", "")

SUPABASE_DB_URL = (
    f"postgresql://postgres:{SUPABASE_SERVICE_ROLE_KEY}"
    f"@{SUPABASE_URL.replace('https://', '')}:6543/postgres"
)


def run_sql_file(sql_path: Path) -> tuple[bool, str]:
    """
    Apply a .sql migration file to Supabase.

    Strategy (in order):
      1. Try psycopg2 (preferred, handles multi-statement SQL properly).
      2. Fall back to Supabase Management REST API (one statement at a time).
    """
    sql_text = sql_path.read_text(encoding="utf-8")
    statements = _split_statements(sql_text)
    if not statements:
        print("INFO: .sql file is empty — nothing to do.")
        return True, ""

    # ---- Strategy 1: psycopg2 ----
    try:
        import psycopg2
        print(f"Using psycopg2 engine...")
        conn = psycopg2.connect(SUPABASE_DB_URL, connect_timeout=10)
        conn.autocommit = True
        cur = conn.cursor()
        errors = []
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                cur.execute(stmt)
                print(f"  OK  {stmt[:70]}...")
            except Exception as e:
                msg = str(e)
                errors.append(msg)
                print(f"  ERR {msg[:100]}")
        cur.close()
        conn.close()
        if errors:
            return False, f"{len(errors)} statement(s) failed:\n" + "\n".join(errors)
        return True, f"All {len(statements)} statements applied via psycopg2."
    except ImportError:
        print("psycopg2 not available, falling back to Management REST API...")
    except Exception as e:
        print(f"psycopg2 connection failed: {e}\nFalling back to REST API...")

    # ---- Strategy 2: Supabase Management API (one statement at a time) ----
    print("Using Supabase Management REST API engine...")
    token = SUPABASE_SERVICE_ROLE_KEY
    # Try project-ref-based Management API endpoint
    project_ref = SUPABASE_URL.replace("https://", "").replace(".supabase.co", "")
    mgmt_url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    errors = []
    applied = 0
    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue
        payload = json.dumps({"sql": stmt}).encode()
        req = urllib.request.Request(
            mgmt_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status in (200, 201, 204):
                    print(f"  OK  {stmt[:70]}...")
                    applied += 1
                else:
                    print(f"  WARN HTTP {resp.status} for: {stmt[:50]}...")
                    applied += 1
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:120]
            except Exception:
                pass
            # 400 on DROP POLICY IF EXISTS is OK — it means the policy wasn't there
            if e.code == 400 and "does not exist" in body:
                print(f"  OK  (already absent) {stmt[:50]}...")
                applied += 1
            else:
                errors.append(f"[HTTP {e.code}] {stmt[:60]}... → {body[:80]}")
                print(f"  ERR [HTTP {e.code}] {body[:80]}")
        except Exception as e:
            errors.append(f"{stmt[:60]}... → {e}")
            print(f"  ERR {e}")
    if errors:
        return False, f"{len(errors)} statement(s) failed:\n" + "\n".join(errors)
    return True, f"All {applied} statements applied via REST API."


def _split_statements(sql: str) -> list[str]:
    """Split raw SQL into individual statements."""
    results = []
    current = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            results.append("\n".join(current))
            current = []
    if current:
        results.append("\n".join(current))
    return results


def test_supabase_connection() -> bool:
    """Verify we can reach Supabase and the key works."""
    print(f"Project URL : {SUPABASE_URL}")
    print(f"Key prefix  : {SUPABASE_SERVICE_ROLE_KEY[:12]}...")
    # Quick probe: fetch the project's REST root
    probe_url = f"{SUPABASE_URL}/rest/v1/"
    req = urllib.request.Request(
        probe_url,
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Supabase reachable — HTTP {resp.status}")
            return True
    except urllib.error.HTTPError as e:
        # 401 without _role=service_role is expected for anon endpoint;
        # 200 means the key works as anon-level. Either is fine for connectivity.
        print(f"Supabase HTTP {e.code} (expected if anon endpoint)")
        return e.code in (200, 401)
    except Exception as e:
        print(f"Cannot reach Supabase: {e}")
        return False


def main():
    print("=" * 60)
    print("R2P — Supabase Schema Migration")
    print("=" * 60)

    if not test_supabase_connection():
        print("\nERROR: Cannot reach Supabase. Check your internet connection and .env.")
        sys.exit(1)

    sql_path = PROJECT_ROOT / "setup_supabase_policies.sql"
    if not sql_path.exists():
        print(f"ERROR: {sql_path} not found.")
        sys.exit(1)

    print(f"\nApplying migration: {sql_path}")
    print("-" * 60)
    ok, msg = run_sql_file(sql_path)
    print("-" * 60)
    if ok:
        print(f"\nSUCCESS: {msg}")
    else:
        print(f"\nPARTIAL FAILURE:\n{msg}")
        print("\nTip: Open Supabase → SQL Editor and paste setup_supabase_policies.sql manually.")


if __name__ == "__main__":
    main()
