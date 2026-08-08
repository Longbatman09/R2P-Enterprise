# R2P Enterprise

**⚠️ Note: This is an under-development project successor to the original R2P (Report_To_Plot) and P2P OLAP.**

An automated, AI-powered platform that extracts student marks from unstructured report files (like PDFs and images) to generate analytical charts and presentation-ready PowerPoint decks — with per-student RAG and school-friendly SDK integration.

## Overview

Extracting marks and performance data manually from unstructured student reports is time-consuming and prone to human error. R2P Enterprise solves this by providing a fully automated workflow. It uses **Google Gemini** for intelligent data extraction, **NVIDIA NIM** for embeddings/chat, **Pinecone** for per-student RAG, and **Model Context Protocol (MCP)** servers to manage modular processing tasks like charting and rendering.

Schools integrate in **one line of code** via the `r2p-school-sdk` Python package — no MCP plumbing required.

## Key Features

- 📄 **AI report parsing** — upload student report PDFs/images; Gemini extracts grades, attendance, and subject performance
- 📊 **Auto charts** — matplotlib-based score visualization per test/exam
- 📽️ **PPTX decks** — presentation-ready output from every analysis
- 🧠 **Per-student RAG** — every student gets an isolated Pinecone namespace; `query_textbook` only returns *their* material
- 🔑 **School API keys** — schools create/revoke `sk_...` keys in the dashboard; used by the SDK
- 🏫 **Multi-tenant** — one Supabase row per school; students, invoices, and report logs all scoped per school
- 💳 **Stripe invoicing** — per-school/plan invoicing via the SDK (`r2p_school_sdk/invoicing`)
- 🔐 **Auth** — Supabase email/password (accounts created by admin), JWT for dashboard, API keys for SDK

## Project Structure

```text
R2P-Enterprise/
├── mcp_backend/            # FastAPI + SSE MCP server (deployed on Render)
│   ├── main.py             # JSON-RPC 2.0 endpoint, unified auth (JWT or sk_ key)
│   ├── auth_routes.py      # /api/auth — login, user API-key store
│   ├── auth_supabase.py    # Supabase JWT verification + AES-GCM key encryption
│   ├── school_routes.py    # /api/schools — schools, students, SDK keys, invoices
│   ├── database.py         # Supabase CRUD for schools/students/keys/invoices/logs
│   ├── API_DOC.md          # contract for the frontend team
│   └── Dockerfile
├── mcp_servers/            # MCP tool modules
│   ├── rag_system.py       # ingest_textbook / query_textbook (per-student namespaces)
│   ├── vision_extractor.py # Gemini report parsing
│   ├── plot_renderer.py    # chart rendering
│   ├── ui_orchestrator.py  # upload/prescan/analyze pipeline tools
│   └── file_watcher.py
├── agents/                 # local pipeline agents (orchestrator, local_mem, …)
├── r2p_school_sdk/         # pip-installable SDK for schools (see README inside)
├── supabase/schema.sql     # run once in Supabase → SQL Editor
├── UI/                     # reference HTML pages (friend builds the real frontend)
├── docker-compose.yml      # local dev stack
└── render.yaml             # Render blueprint
```

## How It Works (The Pipeline)

1. **Upload** — the school app (or SDK) sends a report PDF/base64 to the MCP backend.
2. **Prescan + Extract** — `ui_orchestrator` → `vision_extractor` uses Gemini to pull
   `{exam_name, student_name, student_id, subject, marks}` from each page.
3. **Unify** — `agents/local_mem` merges per-file extractions into one per-student JSON.
4. **Render** — charts are plotted and an optional PPTX deck is generated.
5. **RAG** — separately, textbooks can be ingested into Pinecone under a per-student
   namespace; `query_textbook` embeds the question with NVIDIA NIM, searches that
   student's namespace, and answers with citations.

## Setup & Installation

### Backend (local dev)

```bash
git clone https://github.com/Longbatman09/R2P-Enterprise
cd R2P-Enterprise
cp mcp_backend/.env.local mcp_backend/.env   # fill in real values
docker compose up --build
# → http://localhost:8100/health   200 OK
# → http://localhost:8100/api/auth/login
```

Or run without Docker:

```bash
pip install -r mcp_backend/requirements.txt
export SUPABASE_URL=... SUPABASE_ANON_KEY=... SUPABASE_SERVICE_ROLE_KEY=... \
       JWT_SECRET=... SECRET_KEY=... AES_KEY=$(openssl rand -hex 32)
python mcp_backend/main.py --port 8100
```

### Supabase — one-time setup

1. Create a project at supabase.com.
2. Open **SQL Editor** and paste `supabase/schema.sql` → Run.
3. Note your URL + anon key + service-role key.

### Env vars

| Key | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | yes | project URL |
| `SUPABASE_ANON_KEY` | yes | for login/signup |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | bypasses RLS for admin ops |
| `JWT_SECRET` | yes | 32+ random chars |
| `SECRET_KEY` | yes | 32+ random chars |
| `AES_KEY` | no | 32-byte hex; if unset keys stored base64-only |
| `CORS_ORIGINS` | yes | comma-separated frontend URLs (e.g. `https://r2p-frontend.vercel.app`) |
| `GEMINI_API_KEY` | if report analysis | Google Gemini |
| `NVIDIA_API_KEY` | if RAG | NVIDIA NIM |
| `PINECONE_API_KEY` | if RAG | Pinecone serverless |
| `PINECONE_HOST` | if RAG | index host |
| `PINECONE_INDEX` | if RAG | index name |
| `LLMWHISPERER_API_KEY` | if vision | optional |
| `ADMIN_EMAILS` | no | comma-separated emails allowed to create schools |
| `SIGNUP_ENABLED` | no | set `true` to allow open signup (testing only; default off) |

### Deployment (Render)

1. Connect repo → new **Web Service** → runtime **Docker**.
2. Dockerfile path: `mcp_backend/Dockerfile`, health check `/health`.
3. Add the env vars above, hit **Deploy**.

## 🏫 School SDK — one-line integration

```bash
pip install r2p-school-sdk
```

```python
from r2p_school_sdk import R2PSchoolClient

client = R2PSchoolClient(
    api_url="https://r2p-enterprise.onrender.com",
    api_key="sk-...",          # created in the dashboard
)

# 1. Analyze a report card
result = client.upload_report(
    file_path="report.pdf",
    student_name="Aarav Sharma",
    student_id="10-A",
    output_format="pptx",
)

# 2. Per-student RAG — only that student's material is searched
answer = client.query_textbook(
    textbook_name="grade10-biology",
    question="What is photosynthesis?",
    student_id="10-A",
)
```

See `r2p_school_sdk/README.md` for the full guide (report analysis, RAG,
invoicing, admin dashboard).

## API Quick Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/auth/login` | — | login → JWT |
| `POST /api/auth/signup` | — | create account |
| `GET/PUT/DELETE /api/auth/keys/{service}` | JWT | store user service keys |
| `POST /api/schools/me` | JWT | create/link your school |
| `GET /api/schools/me` | JWT | school + dashboard stats |
| `GET/POST /api/schools/{id}/students` | JWT | manage students |
| `GET/POST /api/schools/{id}/keys` | JWT | create/list SDK keys |
| `PATCH/DELETE /api/schools/{id}/keys/{key_id}` | JWT | rename / revoke |
| `GET /api/schools/{id}/invoices` | JWT | invoices |
| `GET /api/schools/{id}/reports` | JWT | recent report logs |
| `POST /mcp` | JWT **or** `sk_...` | call MCP tools (SDK) |
| `GET /sse` | JWT **or** `sk_...` | SSE event stream |

Full contract: `mcp_backend/API_DOC.md`.

## Technologies Used

- **Backend:** Python, FastAPI, Uvicorn, SSE (MCP JSON-RPC 2.0)
- **AI/OCR:** Google Gemini (extraction), NVIDIA NIM (embeddings + chat)
- **Storage/RAG:** Supabase (auth + Postgres), Pinecone (serverless vector)
- **SDK:** Python package (`requests` only)
- **Invoicing:** Stripe
- **Deploy:** Docker, Render

## Roadmap

- [ ] Frontend (Vercel) — login-only dashboard with school management + API key UI
- [ ] Webhook-based invoice finalization on Stripe payment
- [ ] Multi-exam trend analytics + PDF export
- [ ] Admin API to provision schools/users programmatically

## Credits

- **Base_Software:** B.Vishal Chandrakanth
- **Cloud, Cross Platform Implementation, Future Implementation and Co-Founder:** M.Kabilan

*Licencing is Not Finalized*
