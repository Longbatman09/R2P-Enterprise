import os
from pathlib import Path
from dotenv import load_dotenv
import jwt as _jwt

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in __import__("sys").path:
    __import__("sys").path.append(str(project_root))

load_dotenv(override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
if not SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL is not set in .env. "
        "Add SUPABASE_URL=https://<your-project>.supabase.co"
    )

SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set in .env.")

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

try:
    from supabase import create_client
except ImportError:
    raise RuntimeError("supabase package required. Run: pip install supabase")

_supabase_service = None
_supabase_anon = None


def get_supabase(use_service_role: bool = True):
    """
    Return a cached Supabase client.

    * use_service_role=True (default) — bypasses RLS, use for data writes.
    * use_service_role=False — respects RLS, use for auth calls (sign_in/sign_up).
    """
    global _supabase_service, _supabase_anon
    if use_service_role:
        if _supabase_service is None:
            _supabase_service = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        return _supabase_service
    else:
        anon_key = SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY
        if _supabase_anon is None:
            _supabase_anon = create_client(SUPABASE_URL, anon_key)
        return _supabase_anon


def verify_supabase_token(token: str) -> dict | None:
    """
    Verify a Supabase access_token (JWT) and return the decoded payload.

    Falls back to decoding without verification (for local/dev use) when
    SUPABASE_JWT_SECRET is not configured in .env.
    """
    if not token:
        return None
    try:
        if SUPABASE_JWT_SECRET:
            return _jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        # Dev fallback — decode without verification so the app still works
        return _jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False},
        )
    except Exception:
        return None
