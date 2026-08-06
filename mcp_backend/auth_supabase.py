"""Supabase-backed auth and user-scoped API key store.

Design
------
* Email/password auth signs up + logs in through Supabase Auth.
* The JWT returned by Supabase is used as the bearer token for all
  subsequent MCP-tool calls from the frontend.
* API keys for third-party services (NVIDIA, Pinecone, Gemini, …)
  are **not** stored in plaintext.
  Instead we store an opaque reference token in Supabase Storage,
  and the actual secret lives encrypted in the row.
  For MVP we do a simple AES-GCM encrypt-then-store pattern.
* Each user can only see and use their own keys.

Environment
-----------
SUPABASE_URL          (required)
SUPABASE_ANON_KEY     (required)  – used for signup/login
SUPABASE_SERVICE_ROLE_KEY (required) – bypasses RLS for admin ops
AES_KEY               (32-byte hex or base64) – generated at first deploy
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from supabase import create_client, Client

# ── bootstrap ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("auth_supabase")

# ── Supabase clients ───────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not all([SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY]):
    raise RuntimeError(
        "SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY must be set."
    )

_sb_anon = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
_sb_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ── AES key management ────────────────────────────────────────────────────────
_AES_KEY_HEX = os.environ.get("AES_KEY", "")
if _AES_KEY_HEX:
    _AES_KEY = bytes.fromhex(_AES_KEY_HEX) if len(_AES_KEY_HEX) == 64 else base64.b64decode(_AES_KEY_HEX)
else:
    _AES_KEY = None
    log.warning("AES_KEY not set — API keys will be stored in base64 (not encrypted).")

_aesgcm = AESGCM(_AES_KEY) if _AES_KEY else None


def encrypt(plaintext: str) -> str:
    """AES-GCM encrypt. Returns base64( nonce || ciphertext )."""
    if _aesgcm is None:
        return base64.b64encode(plaintext.encode()).decode()
    nonce = os.urandom(12)
    ct = _aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(token: str) -> str:
    """Reverse of encrypt()."""
    raw = base64.b64decode(token)
    if _aesgcm is None:
        return raw.decode()
    nonce, ct = raw[:12], raw[12:]
    return _aesgcm.decrypt(nonce, ct, None).decode()


# ── token verification ────────────────────────────────────────────────────────
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """FastAPI dependency — validates Supabase JWT and returns the user dict."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing token")
    token = credentials.credentials
    try:
        user_resp = _sb_anon.auth.get_user(token)
        user = user_resp.user
        if user is None:
            raise ValueError("Invalid token")
        return {
            "id": user.id,
            "email": user.email,
            "username": user.user_metadata.get("username") or user.email,
        }
    except Exception as exc:
        log.warning("Token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Only allow users whose email matches ADMIN_EMAILS env var."""
    admins = set(
        e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
    )
    if user["email"] not in admins:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── DB helpers ────────────────────────────────────────────────────────────────

def _upsert_api_key(user_id: str, service: str, key_value: str) -> dict:
    enc = encrypt(key_value)
    payload = {
        "user_id": user_id,
        "service": service,
        "encrypted_key": enc,
        "updated_at": "now()",
    }
    # Use service-role client to bypass RLS
    r = _sb_admin.table("user_api_keys").upsert(
        payload, on_conflict="user_id,service"
    ).execute()
    return r.data[0] if r.data else payload


def _get_api_key(user_id: str, service: str) -> dict | None:
    r = (
        _sb_admin.table("user_api_keys")
        .select("*")
        .eq("user_id", user_id)
        .eq("service", service)
        .single()
        .execute()
    )
    row = r.data
    if row and row.get("encrypted_key"):
        row["key"] = decrypt(row["encrypted_key"])
    return row


def _list_api_keys(user_id: str) -> list[dict]:
    r = (
        _sb_admin.table("user_api_keys")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )
    rows = r.data or []
    for row in rows:
        if row.get("encrypted_key"):
            row["key"] = decrypt(row["encrypted_key"])
        row.pop("encrypted_key", None)
    return rows


def _delete_api_key(user_id: str, service: str) -> None:
    _sb_admin.table("user_api_keys").delete().eq("user_id", user_id).eq("service", service).execute()
