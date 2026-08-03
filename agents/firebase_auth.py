"""
Token verification helpers.

The application's primary authentication is Supabase Auth. This module
wraps Supabase JWT verification so that the rest of the codebase can import
from a single location (``agents.firebase_auth``) without changing its
import path, while actually using Supabase under the hood.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in __import__("sys").path:
    __import__("sys").path.append(str(project_root))

load_dotenv(override=True)

from agents.supabase_client import verify_supabase_token  # noqa: E402


def verify_id_token(id_token: str) -> dict | None:
    """
    Verify the supplied bearer token and return the decoded payload.

    Under the hood this delegates to ``agents.supabase_client.verify_supabase_token``
    which handles both strict verification (when SUPABASE_JWT_SECRET is set) and
    a dev-mode fallback.
    """
    if not id_token:
        return None
    return verify_supabase_token(id_token)
