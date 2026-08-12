"""Supabase client bootstrap and high-level CRUD helpers.

Tables expected in Supabase (run the SQL in supabase/schema.sql first):

  school_api_keys – opaque integration API keys per school (SDK keys, sk_...)
  schools         – one row per tenant
  students        – one row per student (linked to a school)
  invoices        – Stripe invoice records per school
  report_logs     – audit log of uploaded/analyzed reports

Note: school_api_keys is a *different* table from user_api_keys
(per-user third-party service keys used by the dashboard).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client

log = logging.getLogger("r2p.db")

# ── bootstrap ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
    )

_sb: Client | None = None


def client() -> Client:
    """Return a singleton Supabase service-role client."""
    global _sb
    if _sb is None:
        _sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _sb


# ── raw table helpers ──────────────────────────────────────────────────────────

def _t(name: str):
    return client().table(name)


# ── Schools ────────────────────────────────────────────────────────────────────

def get_or_create_school(name: str, contact_email: str, plan: str = "basic") -> dict:
    """Find a school by name, or create it if missing."""
    sb = client()
    res = sb.table("schools").select("*").eq("name", name).execute()
    if res.data:
        return res.data[0]
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "name": name,
        "contact_email": contact_email,
        "plan": plan,
        "created_at": now,
    }
    ins = sb.table("schools").insert(row).execute()
    return ins.data[0]


def list_schools() -> list[dict]:
    return _t("schools").select("*").execute().data


def get_school(school_id: str) -> dict | None:
    res = _t("schools").select("*").eq("id", school_id).execute().data
    return res[0] if res else None


# ── Students ──────────────────────────────────────────────────────────────────

def list_students(school_id: str) -> list[dict]:
    return (
        _t("students")
        .select("*")
        .eq("school_id", school_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )


def add_student(school_id: str, name: str, grade: str = "") -> dict:
    row = {
        "school_id": school_id,
        "name": name,
        "grade": grade,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    ins = _t("students").insert(row).execute()
    return ins.data[0]


def delete_student(student_id: str, school_id: str | None = None) -> None:
    q = _t("students").delete().eq("id", student_id)
    if school_id:
        q = q.eq("school_id", school_id)
    q.execute()


def count_students(school_id: str) -> int:
    res = (
        _t("students")
        .select("id", count="exact")
        .eq("school_id", school_id)
        .execute()
    )
    return res.count or 0


# ── Integration API keys (school SDK keys, sk_...) ────────────────────────────

def create_api_key(
    school_id: str,
    key_hash: str,
    key_prefix: str,
    name: str = "default",
    created_by: str = "",
    scopes: list[str] | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "school_id": school_id,
        "key_hash": key_hash,
        "key_prefix": key_prefix,
        "name": name,
        "created_by": created_by,
        "scopes": scopes or ["reports", "rag", "chat"],
        "is_active": True,
        "created_at": now,
        "last_used_at": None,
    }
    ins = _t("school_api_keys").insert(row).execute()
    return ins.data[0]


def list_api_keys(school_id: str) -> list[dict]:
    return (
        _t("school_api_keys")
        .select("*")
        .eq("school_id", school_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )


def find_active_key(key_hash: str) -> dict | None:
    res = (
        _t("school_api_keys")
        .select("*")
        .eq("key_hash", key_hash)
        .eq("is_active", True)
        .execute()
        .data
    )
    if not res:
        return None
    key = res[0]
    _t("school_api_keys").update(
        {"last_used_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", key["id"]).execute()
    return key


def revoke_api_key(key_id: str, school_id: str | None = None) -> None:
    q = _t("school_api_keys").update({"is_active": False}).eq("id", key_id)
    if school_id:
        q = q.eq("school_id", school_id)
    q.execute()


def delete_api_key(key_id: str, school_id: str | None = None) -> None:
    q = _t("school_api_keys").delete().eq("id", key_id)
    if school_id:
        q = q.eq("school_id", school_id)
    q.execute()


# ── User ↔ school linking ────────────────────────────────────────────────────

def get_profile(user_id: str) -> dict | None:
    res = _t("profiles").select("*").eq("id", user_id).maybe_single().execute()
    return res.data if res.data else None


def get_school_for_user(user_id: str) -> dict | None:
    """Return the school linked to this user's profile (if any)."""
    profile = get_profile(user_id)
    if not profile or not profile.get("school_id"):
        return None
    return get_school(profile["school_id"])


def link_user_to_school(user_id: str, school_id: str) -> None:
    """Link a user to a school. Uses UPSERT so accounts created in the
    Supabase dashboard *before* the profiles trigger existed still get a
    profile row instead of silently failing to link."""
    _t("profiles").upsert(
        {"id": user_id, "school_id": school_id},
        on_conflict="id",
    ).execute()


def unlink_user_from_school(user_id: str) -> None:
    _t("profiles").update({"school_id": None}).eq("id", user_id).execute()


# ── Invoices ──────────────────────────────────────────────────────────────────

def create_invoice(
    school_id: str,
    amount_cents: int,
    currency: str = "usd",
    stripe_invoice_id: str | None = None,
    status: str = "draft",
    pdf_url: str | None = None,
    metadata: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row: dict[str, Any] = {
        "school_id": school_id,
        "amount_cents": amount_cents,
        "currency": currency,
        "status": status,
        "stripe_invoice_id": stripe_invoice_id,
        "pdf_url": pdf_url,
        "metadata": metadata or {},
        "created_at": now,
        "updated_at": now,
    }
    ins = _t("invoices").insert(row).execute()
    return ins.data[0]


def list_invoices(school_id: str | None = None) -> list[dict]:
    q = _t("invoices").select("*")
    if school_id:
        q = q.eq("school_id", school_id)
    return q.order("created_at", desc=True).execute().data


def get_invoice(invoice_id: str) -> dict | None:
    res = _t("invoices").select("*").eq("id", invoice_id).execute().data
    return res[0] if res else None


def update_invoice_status(invoice_id: str, status: str, stripe_invoice_id: str | None = None) -> dict:
    patch: dict[str, Any] = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
    if stripe_invoice_id:
        patch["stripe_invoice_id"] = stripe_invoice_id
    res = _t("invoices").update(patch).eq("id", invoice_id).execute()
    return res.data[0]


# ── Report logs ────────────────────────────────────────────────────────────────

def log_report(
    school_id: str,
    student_id: str,
    student_name: str,
    file_name: str,
    pages: int = 0,
) -> dict:
    row = {
        "school_id": school_id,
        "student_id": student_id,
        "student_name": student_name,
        "file_name": file_name,
        "pages": pages,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    ins = _t("report_logs").insert(row).execute()
    return ins.data[0]


def count_reports(school_id: str | None = None, student_id: str | None = None) -> int:
    q = _t("report_logs").select("id", count="exact")
    if school_id:
        q = q.eq("school_id", school_id)
    if student_id:
        q = q.eq("student_id", student_id)
    return q.execute().count or 0
