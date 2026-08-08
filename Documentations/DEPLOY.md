# R2P-Enterprise — One-Shot Deploy Guide

## Local (Mac) — fastest path

```bash
git clone ...
cp mcp_backend/.env.local mcp_backend/.env   # fill in real values
docker compose up --build
# → http://localhost:8100/health   200 OK
# → http://localhost:8100/api/auth/signup
```

## Render — one-click Web Service

1. Connect repo `https://github.com/Longbatman09/R2P-Enterprise`
2. Runtime: **Docker**
3. Dockerfile path: `mcp_backend/Dockerfile`
4. Start command: *(leave blank — Docker CMD handles it)*
5. Health check path: `/health`
6. Add these env vars (from your Supabase / Pinecone / NVIDIA dashboard):

| Key | Required |
|---|---|
| `SUPABASE_URL` | yes |
| `SUPABASE_ANON_KEY` | yes |
| `SUPABASE_SERVICE_ROLE_KEY` | yes |
| `JWT_SECRET` | yes — any random 32+ char string |
| `SECRET_KEY` | yes — any random 32+ char string |
| `CORS_ORIGINS` | yes — comma-separated frontend URLs |
| `PINECONE_API_KEY` | if using RAG |
| `PINECONE_HOST` | if using RAG |
| `PINECONE_INDEX` | if using RAG |
| `NVIDIA_API_KEY` | if using RAG |
| `GEMINI_API_KEY` | if using report analysis |
| `LLMWHISPERER_API_KEY` | if using vision |
| `LOG_LEVEL` | no — defaults to `INFO` |

7. Hit **Deploy**. First build takes ~5 min. Watch logs for `Application startup complete`.

## Supabase — run this SQL once

Open **Supabase → SQL Editor** and run the entire contents of
`supabase/schema.sql` (idempotent — safe to re-run). It creates:

* `schools`, `students`, `invoices`, `report_logs` — tenant data
* `school_api_keys` — SDK integration keys (hash + prefix stored)
* `user_api_keys` — per-user third-party service keys (dashboard)
* `profiles` (+ `school_id` link) with auto-create trigger
* Row-Level-Security policies scoped to the logged-in user's school
* `current_user_school_id()` helper used by the policies

## School integration — one-liner

See `r2p_school_sdk/` for the pip-installable SDK.
