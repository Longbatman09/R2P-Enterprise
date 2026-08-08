"""REST endpoints for school (tenant) management and SDK API keys.

Mounted under /api/schools in main.py.

Auth
----
All endpoints require a logged-in Supabase user (JWT via `get_current_user`).
The user must be linked to the school they operate on (see schema.sql's
`profiles.school_id` + RLS policies). Service-role server calls bypass RLS.

Key flow
--------
1. Admin creates the school user manually in Supabase Auth (email/password).
2. School user logs in via the frontend and links a school (POST /api/schools/me).
3. School user creates integration keys (POST /api/schools/{id}/keys).
4. The plaintext key is returned ONCE; only its SHA-256 hash is stored.
5. School apps use the key as `Authorization: Bearer sk_...` on /mcp.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database as db
from auth_supabase import get_current_user

log = logging.getLogger("school_routes")
router = APIRouter()

KEY_PREFIX = "sk_"


# ── request schemas ────────────────────────────────────────────────────────────

class SchoolCreate(BaseModel):
    name: str
    contact_email: str = ""
    plan: str = "basic"


class SchoolUpdate(BaseModel):
    name: str | None = None
    contact_email: str | None = None
    plan: str | None = None


class StudentCreate(BaseModel):
    name: str
    grade: str = ""


class ApiKeyCreate(BaseModel):
    name: str = "default"
    scopes: list[str] | None = None


class ApiKeyUpdate(BaseModel):
    is_active: bool | None = None
    name: str | None = None


# ── helpers ───────────────────────────────────────────────────────────────────

def _hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def _new_api_key() -> tuple[str, str, str]:
    """Generate (plaintext, prefix, hash) for a new sk_... key."""
    plain = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plain, plain[:12], _hash_key(plain)


def _require_school(school_id: str) -> dict:
    school = db.get_school(school_id)
    if not school:
        raise HTTPException(404, "School not found")
    return school


def _require_user_school(user: dict) -> dict:
    """The school the current user is linked to, or 409 if not linked yet."""
    school = db.get_school_for_user(user["id"])
    if not school:
        raise HTTPException(
            409,
            "This account is not linked to a school yet. "
            "Call POST /api/schools/me to create or link one.",
        )
    return school


VALID_PLANS = {"basic", "pro", "enterprise"}


def _check_school_ownership(user: dict, school_id: str) -> dict:
    """Return the user's school, raising 403 unless it matches school_id."""
    mine = _require_user_school(user)
    if str(mine["id"]) != school_id:
        raise HTTPException(403, "You can only access your own school")
    return mine


# ── schools ───────────────────────────────────────────────────────────────────

@router.post("/me", status_code=201)
async def create_or_get_my_school(req: SchoolCreate, user: dict = Depends(get_current_user)):
    """Create a school and link it to the current user (idempotent)."""
    if req.plan not in VALID_PLANS:
        raise HTTPException(400, f"plan must be one of {sorted(VALID_PLANS)}")
    existing = db.get_school_for_user(user["id"])
    if existing:
        return {"school": existing, "created": False}

    school = db.get_or_create_school(
        req.name, req.contact_email or user.get("email", ""), req.plan
    )
    db.link_user_to_school(user["id"], school["id"])
    return {"school": school, "created": True}


@router.get("/me")
async def get_my_school(user: dict = Depends(get_current_user)):
    """The current user's school + quick dashboard stats."""
    school = _require_user_school(user)
    school_id = school["id"]
    return {
        "school": school,
        "stats": {
            "students": db.count_students(school_id),
            "reports": db.count_reports(school_id),
            "api_keys": len(db.list_api_keys(school_id)),
            "invoices": len(db.list_invoices(school_id)),
        },
    }


@router.get("/{school_id}")
async def get_school(school_id: str, user: dict = Depends(get_current_user)):
    """Fetch a school by id (only if the user belongs to it)."""
    _check_school_ownership(user, school_id)
    return _require_school(school_id)


@router.patch("/{school_id}")
async def update_school(
    school_id: str, req: SchoolUpdate, user: dict = Depends(get_current_user)
):
    if req.plan is not None and req.plan not in VALID_PLANS:
        raise HTTPException(400, f"plan must be one of {sorted(VALID_PLANS)}")
    _check_school_ownership(user, school_id)
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if not patch:
        return _require_school(school_id)
    res = db.client().table("schools").update(patch).eq("id", school_id).execute()
    return res.data[0]


# ── students ──────────────────────────────────────────────────────────────────

@router.get("/{school_id}/students")
async def list_students(school_id: str, user: dict = Depends(get_current_user)):
    _check_school_ownership(user, school_id)
    return db.list_students(school_id)


@router.post("/{school_id}/students", status_code=201)
async def add_student(
    school_id: str, req: StudentCreate, user: dict = Depends(get_current_user)
):
    _check_school_ownership(user, school_id)
    return db.add_student(school_id, req.name, req.grade)


@router.delete("/{school_id}/students/{student_id}", status_code=204)
async def remove_student(
    school_id: str, student_id: str, user: dict = Depends(get_current_user)
):
    _check_school_ownership(user, school_id)
    db.delete_student(student_id, school_id=school_id)
    return None


# ── SDK API keys ──────────────────────────────────────────────────────────────

@router.get("/{school_id}/keys")
async def list_school_keys(school_id: str, user: dict = Depends(get_current_user)):
    """List keys for a school. Never returns the full key — only prefix."""
    _check_school_ownership(user, school_id)
    keys = db.list_api_keys(school_id)
    for k in keys:
        k.pop("key_hash", None)
    return {"keys": keys}


@router.post("/{school_id}/keys", status_code=201)
async def create_school_key(
    school_id: str, req: ApiKeyCreate, user: dict = Depends(get_current_user)
):
    """Create a new SDK integration key. Returns the plaintext key ONCE."""
    _check_school_ownership(user, school_id)
    plain, prefix, key_hash = _new_api_key()
    row = db.create_api_key(
        school_id=school_id,
        key_hash=key_hash,
        key_prefix=prefix,
        name=req.name,
        created_by=user["id"],
        scopes=req.scopes,
    )
    return {
        "key": plain,          # show once — not retrievable again
        "key_prefix": prefix,
        "name": req.name,
        "scopes": req.scopes or ["reports", "rag", "chat"],
        "id": row["id"],
    }


@router.patch("/{school_id}/keys/{key_id}")
async def update_school_key(
    school_id: str, key_id: str, req: ApiKeyUpdate, user: dict = Depends(get_current_user)
):
    _check_school_ownership(user, school_id)
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(400, "Nothing to update")
    res = (
        db.client()
        .table("school_api_keys")
        .update(patch)
        .eq("id", key_id)
        .eq("school_id", school_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Key not found")
    row = res.data[0]
    row.pop("key_hash", None)
    return row


@router.delete("/{school_id}/keys/{key_id}", status_code=204)
async def revoke_school_key(
    school_id: str, key_id: str, user: dict = Depends(get_current_user)
):
    """Revoke (deactivate) a key so it can no longer call /mcp."""
    _check_school_ownership(user, school_id)
    db.revoke_api_key(key_id, school_id=school_id)
    return None


# ── invoices + report logs (dashboard data) ───────────────────────────────────

@router.get("/{school_id}/invoices")
async def list_school_invoices(school_id: str, user: dict = Depends(get_current_user)):
    _check_school_ownership(user, school_id)
    return {"invoices": db.list_invoices(school_id)}


@router.get("/{school_id}/reports")
async def list_school_reports(school_id: str, user: dict = Depends(get_current_user)):
    _check_school_ownership(user, school_id)
    res = (
        db.client()
        .table("report_logs")
        .select("*")
        .eq("school_id", school_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return {"reports": res.data}
