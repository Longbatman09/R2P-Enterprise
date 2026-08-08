"""REST endpoints for auth + API key management.

All endpoints are mounted under /api/auth and /api/keys in main.py.

Auth schemes
------------
Header  Authorization: Bearer <supabase_jwt>
        or
Query   ?token=<supabase_jwt>

Key management
--------------
Stored encrypted in Supabase table `user_api_keys` via service-role RPC.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from auth_supabase import (
    decrypt,
    encrypt,
    get_current_user,
    _delete_api_key,
    _get_api_key,
    _list_api_keys,
    _upsert_api_key,
)

log = logging.getLogger("auth_routes")
router = APIRouter()

# Services we manage keys for
KNOWN_SERVICES = [
    "supabase",
    "pinecone",
    "nvidia_nim",
    "nvidia",
    "gemini",
    "llmwhisperer",
    "firebase",
]


# ── request / response schemas ────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str
    username: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class KeyUpsertRequest(BaseModel):
    service: str
    key: str


class KeyResponse(BaseModel):
    service: str
    key: str
    created_at: str | None = None
    updated_at: str | None = None


class KeysListResponse(BaseModel):
    keys: list[KeyResponse]


# ── helpers ───────────────────────────────────────────────────────────────────

def _mask(s: str, keep: int = 4) -> str:
    if not s:
        return ""
    return s[:keep] + "*" * max(0, len(s) - keep)


async def _user_from_token(request: Request) -> dict:
    """Extracts current user from JWT via header or query param."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    else:
        token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(401, "Missing token")
    return await get_current_user.__wrapped__(  # type: ignore[attr-defined]
        __import__("fastapi.security").security.HTTPAuthorizationCredentials(  # type: ignore[attr-defined]
            scheme="Bearer",
            credentials=token,
        )
    )


# ── signup / login ────────────────────────────────────────────────────────────

@router.post("/signup")
async def signup(req: SignupRequest):
    """Create a new user in Supabase Auth and log them in.

    Per the product's auth model there is NO public self-service signup —
    accounts are created manually by the platform admin. Set
    SIGNUP_ENABLED=true to allow open signup (e.g. for testing).
    """
    if os.environ.get("SIGNUP_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=403,
            detail=("Self-service signup is disabled. "
                    "Contact the platform admin to create your account."),
        )
    from auth_supabase import _sb_anon
    try:
        result = _sb_anon.auth.sign_up({
            "email": req.email,
            "password": req.password,
        })
        if result.user:
            _sb_anon.table("profiles").upsert({
                "id": result.user.id,
                "email": req.email,
                "username": req.username or req.email.split("@")[0],
            }).execute()
        return {"user": result.user, "session": result.session}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login")
async def login(req: LoginRequest):
    """Log in existing user."""
    from auth_supabase import _sb_anon
    try:
        result = _sb_anon.auth.sign_in_with_password({
            "email": req.email,
            "password": req.password,
        })
        return {
            "access_token": result.session.access_token,
            "refresh_token": result.session.refresh_token,
            "user": result.user,
        }
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc


@router.post("/logout")
async def logout(user: dict = Depends(get_current_user)):
    """Log out current user."""
    from auth_supabase import _sb_anon
    try:
        _sb_anon.auth.sign_out()
    except Exception:
        pass
    return {"status": "ok"}


# ── API key management ────────────────────────────────────────────────────────

@router.get("/keys", response_model=KeysListResponse)
async def list_keys(user: dict = Depends(get_current_user)):
    keys = _list_api_keys(user["id"])
    return KeysListResponse(keys=[
        KeyResponse(
            service=k.get("service", ""),
            key=_mask(k.get("key", "")),
            created_at=k.get("created_at"),
            updated_at=k.get("updated_at"),
        )
        for k in keys
    ])


@router.get("/keys/{service}", response_model=KeyResponse)
async def get_key(service: str, user: dict = Depends(get_current_user)):
    from auth_supabase import decrypt
    row = _get_api_key(user["id"], service)
    if not row:
        raise HTTPException(404, f"No key stored for service '{service}'")
    return KeyResponse(
        service=row.get("service", service),
        key=decrypt(row.get("encrypted_key", "")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.put("/keys/{service}", response_model=KeyResponse)
async def upsert_key(service: str, req: KeyUpsertRequest, user: dict = Depends(get_current_user)):
    if service != req.service:
        raise HTTPException(400, "URL service must match body service")
    row = _upsert_api_key(user["id"], service, req.key)
    return KeyResponse(
        service=row.get("service", service),
        key=_mask(row.get("encrypted_key", ""), keep=0),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.delete("/keys/{service}")
async def delete_key(service: str, user: dict = Depends(get_current_user)):
    _delete_api_key(user["id"], service)
    return {"status": "deleted", "service": service}
