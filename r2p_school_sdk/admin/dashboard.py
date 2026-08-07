"""
R2P School Admin Dashboard — drop-in FastAPI sub-app.

Mount it on your existing FastAPI app so the school IT admin can:
  * see all schools enrolled in the R2P platform,
  * list students per school,
  * inspect per-student usage,
  * manage invoices.

Usage (server-side):
    from r2p_school_sdk.admin.dashboard import admin_app
    app.mount("/admin", admin_app)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("r2p.admin")

admin_app = FastAPI(
    title="R2P School Admin",
    description="School-facing admin dashboard for R2P-Enterprise",
    version="0.1.0",
)

# In-memory demo store — swap for Supabase in production.
_schools: dict[str, dict[str, Any]] = {
    "school-demo": {
        "name": "Demo High School",
        "contact_email": "admin@demohigh.edu",
        "plan": "pro",
        "student_count": 240,
        "created_at": "2026-01-01T00:00:00Z",
    }
}

_students: dict[str, list[dict[str, Any]]] = {
    "school-demo": [
        {"id": "grade10-aarav", "name": "Aarav Sharma", "grade": "10A"},
        {"id": "grade10-priya", "name": "Priya Verma", "grade": "10B"},
    ]
}

_invoices: list[dict[str, Any]] = [
    {
        "id": "inv_demo_1",
        "school_id": "school-demo",
        "amount_cents": 499900,
        "currency": "usd",
        "status": "paid",
        "created_at": "2026-07-01T00:00:00Z",
    }
]

_usage: dict[str, dict[str, int]] = {
    "grade10-aarav": {"reports_analyzed": 12, "rag_queries": 87, "storage_mb": 340},
    "grade10-priya": {"reports_analyzed": 8, "rag_queries": 42, "storage_mb": 210},
}


# ------------------------------------------------------------------
# Schools
# ------------------------------------------------------------------

@admin_app.get("/schools")
async def list_schools() -> JSONResponse:
    return JSONResponse(list(_schools.values()))


@admin_app.get("/schools/{school_id}")
async def get_school(school_id: str) -> JSONResponse:
    school = _schools.get(school_id)
    if not school:
        raise HTTPException(404, "School not found")
    return JSONResponse(school)


# ------------------------------------------------------------------
# Students
# ------------------------------------------------------------------

@admin_app.get("/schools/{school_id}/students")
async def list_students(school_id: str) -> JSONResponse:
    students = _students.get(school_id, [])
    # Attach usage
    out = []
    for s in students:
        d = dict(s)
        d["usage"] = _usage.get(s["id"], {"reports_analyzed": 0, "rag_queries": 0, "storage_mb": 0})
        out.append(d)
    return JSONResponse(out)


@admin_app.post("/schools/{school_id}/students")
async def add_student(school_id: str, body: dict[str, Any]) -> JSONResponse:
    if school_id not in _schools:
        raise HTTPException(404, "School not found")
    student = {
        "id": body["id"],
        "name": body["name"],
        "grade": body.get("grade", ""),
    }
    _students.setdefault(school_id, []).append(student)
    return JSONResponse(student, status_code=201)


@admin_app.delete("/schools/{school_id}/students/{student_id}")
async def delete_student(school_id: str, student_id: str) -> JSONResponse:
    lst = _students.get(school_id, [])
    _students[school_id] = [s for s in lst if s["id"] != student_id]
    return JSONResponse({"deleted": True})


# ------------------------------------------------------------------
# Usage
# ------------------------------------------------------------------

@admin_app.get("/students/{student_id}/usage")
async def get_student_usage(student_id: str) -> JSONResponse:
    return JSONResponse(_usage.get(student_id, {}))


@admin_app.get("/schools/{school_id}/usage")
async def get_school_usage(school_id: str) -> JSONResponse:
    students = _students.get(school_id, [])
    total_reports = 0
    total_queries = 0
    total_storage = 0
    for s in students:
        u = _usage.get(s["id"], {})
        total_reports += u.get("reports_analyzed", 0)
        total_queries += u.get("rag_queries", 0)
        total_storage += u.get("storage_mb", 0)
    return JSONResponse({
        "school_id": school_id,
        "reports_analyzed": total_reports,
        "rag_queries": total_queries,
        "storage_mb": total_storage,
    })


# ------------------------------------------------------------------
# Invoices
# ------------------------------------------------------------------

@admin_app.get("/invoices")
async def list_invoices(school_id: str | None = Query(None)) -> JSONResponse:
    items = _invoices
    if school_id:
        items = [i for i in items if i["school_id"] == school_id]
    return JSONResponse(items)


@admin_app.post("/invoices")
async def create_invoice(body: dict[str, Any]) -> JSONResponse:
    inv = {
        "id": f"inv_{body['school_id']}_{datetime.now(timezone.utc).timestamp()}",
        "school_id": body["school_id"],
        "amount_cents": body["amount_cents"],
        "currency": body.get("currency", "usd"),
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _invoices.append(inv)
    return JSONResponse(inv, status_code=201)
