# R2P School SDK

One-liner Python SDK for plugging R2P-Enterprise into any school app or website.
Your school only needs an **API key** (created in the dashboard) — nothing else.

## Install

```bash
pip install r2p-school-sdk
```

Or for local dev:

```bash
cd /path/to/R2P-Enterprise/r2p_school_sdk
pip install -e .
```

## Quickstart

```python
from r2p_school_sdk import R2PSchoolClient

client = R2PSchoolClient(
    api_url="https://r2p-enterprise.onrender.com",
    api_key="sk-...",          # created in the dashboard → API Keys
)

result = client.upload_report(
    file_path="report.pdf",
    student_name="Aarav Sharma",
    student_id="grade10-aarav",
    output_format="pptx",      # pptx | charts | both
    wait=True,                 # block until the pipeline finishes
)
print(result)
```

That single call runs the whole pipeline: **upload → Gemini extraction →
unified per-student JSON → charts → PPTX**. No MCP knowledge needed.

## Per-student RAG

Each student gets a **private Pinecone namespace** in the school's index, so
`query_textbook` always returns results from that student's own ingested
materials only:

```python
# Index the student's own study material into their private namespace
client.ingest_textbook(
    file_path="grade10-biology.pdf",
    textbook_name="grade10-biology",
    student_id="grade10-aarav",     # <- isolation!
)

# Ask a question — only the student's namespace is searched
answer = client.query_textbook(
    textbook_name="grade10-biology",
    question="What is photosynthesis?",
    student_id="grade10-aarav",
    top_k=5,
)
print(answer["answer"])
print(answer["sources"])            # citations with page numbers
```

If `student_id` is omitted, the shared (school-wide) namespace is used.

## Async / non-blocking analysis

`upload_report(..., wait=False)` returns immediately with `{"status": "started"}`.
Poll with:

```python
started = client.upload_report(..., wait=False)
state = client.wait_for_report(timeout=300)   # polls until completed
# or manually:
state = client.get_pipeline_state()
```

## Other methods

| Method | Purpose |
|---|---|
| `upload_report(...)` | full analysis pipeline for a report PDF |
| `get_pipeline_state()` | current pipeline stage |
| `wait_for_report(timeout=)` | block until pipeline completes |
| `ingest_textbook(...)` | index a textbook (optionally per student) |
| `query_textbook(...)` | RAG question with citations |
| `list_textbooks()` | list ingested textbooks |
| `delete_textbook(name)` | remove a textbook |
| `rag_health()` | sanity-check the RAG stack |
| `list_tools()` | list all MCP tools exposed by the backend |

## Auth

- SDK calls use the school's **integration key**: `Authorization: Bearer sk_...`
- Keys are created + revoked in the dashboard (`/api/schools/{id}/keys`)
- A revoked key stops working immediately (403/401 on next call)

## Errors

- `PermissionError` — 401: key invalid/revoked/wrong school
- `RuntimeError` — tool-level failure (check `.error` message)
- `TimeoutError` — `wait_for_report` exceeded its timeout

## Admin dashboard (for the school's staff)

The SDK ships an optional FastAPI sub-app you can mount on your own server:

```python
from r2p_school_sdk.admin.dashboard import admin_app
app.mount("/admin", admin_app)
```

Endpoints: `GET /admin/schools`, `GET /admin/schools/{id}/students`,
`GET /admin/students/{id}/usage`, `GET|POST /admin/invoices`.
See `admin/dashboard.py` for the full surface.

## Invoicing

`r2p_school_sdk.invoicing.stripe_service` wraps Stripe invoice creation
(create / fetch / void). It's used by the platform backend per school plan.
