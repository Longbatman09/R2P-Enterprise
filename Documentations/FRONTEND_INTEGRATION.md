# R2P-Enterprise — Frontend Integration Guide

> **For the frontend developer.** This document is everything you need to build
> the web app (Vercel) that talks to the R2P-Enterprise backend (Render).

---

## 1. Backend connection details

| Item | Value |
|---|---|
| **Base URL** | `https://r2p-enterprise.onrender.com` |
| **Protocol** | HTTPS (REST + JSON + SSE) |
| **Auth** | Supabase email/password → JWT bearer token |
| **CORS** | Configured to allow your Vercel domain (`CORS_ORIGINS` env var) |

> ⚠️ Use the **real URL** `https://r2p-enterprise.onrender.com` everywhere.
> Do not use `localhost` — the backend is deployed.

---

## 2. Auth model (IMPORTANT — read first)

- **No public signup.** The platform admin creates each school's account manually
  in Supabase Auth (email + password). The frontend is **login-only**.
- The school user logs in with their credentials and receives a **JWT**.
- The JWT is sent as `Authorization: Bearer <jwt>` for all dashboard calls.
- The school user creates **integration API keys** (`sk_...`) used by the school
  SDK — these are *not* the frontend auth tokens.

### 2.1 Login

```
POST https://r2p-enterprise.onrender.com/api/auth/login
Content-Type: application/json
```

```json
{ "email": "office@greenwood.edu", "password": "their-password" }
```

**Response 200:**

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "user": { "id": "uuid", "email": "office@greenwood.edu" }
}
```

**Store `access_token`** (localStorage or httpOnly cookie) and send it on every
subsequent request:

```
Authorization: Bearer eyJhbGciOi...
```

**Error:** `401 Invalid credentials`

### 2.2 Logout

```
POST https://r2p-enterprise.onrender.com/api/auth/logout   (JWT required)
```

---

## 3. First-run: link/create your school

After login, the user must be linked to a school. Call this once — it creates the
school (if needed) and links it to the user's profile. Idempotent.

```
POST https://r2p-enterprise.onrender.com/api/schools/me
Authorization: Bearer <jwt>
Content-Type: application/json
```

```json
{
  "name": "Greenwood Public School",
  "contact_email": "office@greenwood.edu",
  "plan": "pro"
}
```

**Response 201:**

```json
{
  "school": { "id": "uuid", "name": "Greenwood Public School", "contact_email": "...", "plan": "pro", "created_at": "..." },
  "created": true
}
```

> If the user already has a school, the same call returns `created: false` with
> the existing school.

---

## 4. Dashboard data

### 4.1 My school + stats

```
GET https://r2p-enterprise.onrender.com/api/schools/me
Authorization: Bearer <jwt>
```

```json
{
  "school": { "id": "...", "name": "...", "plan": "..." },
  "stats": { "students": 12, "reports": 87, "api_keys": 3, "invoices": 5 }
}
```

### 4.2 Students

```
GET    https://r2p-enterprise.onrender.com/api/schools/{school_id}/students
POST   https://r2p-enterprise.onrender.com/api/schools/{school_id}/students   → { "name": "Aarav", "grade": "10" }
DELETE https://r2p-enterprise.onrender.com/api/schools/{school_id}/students/{student_id}
```

### 4.3 Reports (audit log)

```
GET https://r2p-enterprise.onrender.com/api/schools/{school_id}/reports
```

### 4.4 Invoices

```
GET https://r2p-enterprise.onrender.com/api/schools/{school_id}/invoices
```

---

## 5. SDK API-key management (the core feature)

The school user creates the keys that their school apps use with the SDK.

### 5.1 Create a key

```
POST https://r2p-enterprise.onrender.com/api/schools/{school_id}/keys
Authorization: Bearer <jwt>
```

```json
{ "name": "School ERP", "scopes": ["reports", "rag", "chat"] }
```

**Response 201 — the plaintext key is shown EXACTLY ONCE:**

```json
{
  "key": "sk_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
  "key_prefix": "sk_xxxxxxxxx",
  "name": "School ERP",
  "scopes": ["reports", "rag", "chat"],
  "id": "uuid"
}
```

> **UI requirement:** show the key in a one-time modal with a "Copy" button and a
> warning that it won't be shown again.

### 5.2 List keys (masked)

```
GET https://r2p-enterprise.onrender.com/api/schools/{school_id}/keys
```

```json
{
  "keys": [
    { "id": "uuid", "name": "School ERP", "key_prefix": "sk_xxxxxxxxx", "scopes": [...], "is_active": true, "created_at": "...", "last_used_at": "..." }
  ]
}
```

### 5.3 Rename / activate / deactivate

```
PATCH https://r2p-enterprise.onrender.com/api/schools/{school_id}/keys/{key_id}
```

```json
{ "name": "New name", "is_active": false }
```

### 5.4 Revoke (delete)

```
DELETE https://r2p-enterprise.onrender.com/api/schools/{school_id}/keys/{key_id}
```

> Revoked keys immediately stop working (SDK calls return `401`).

---

## 6. Calling the MCP tools (report analysis & RAG)

The frontend can call the same tools the SDK uses, directly via `/mcp` (JSON-RPC
2.0). Use the **JWT** here (frontend). School apps use `sk_...` keys instead.

### 6.1 List available tools

```
POST https://r2p-enterprise.onrender.com/mcp
Authorization: Bearer <jwt>
Content-Type: application/json
```

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/list" }
```

### 6.2 Call a tool

```
POST https://r2p-enterprise.onrender.com/mcp
```

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "analyze_reports",
    "arguments": {
      "selected_files": ["report.pdf"],
      "output_format": "pptx",
      "extra_description": "",
      "student_name": "Aarav Sharma",
      "student_id": "10-A"
    }
  }
}
```

### 6.3 Upload a file first

`analyze_reports` expects **server-side file names**, so upload first:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "upload_files",
    "arguments": {
      "files": [{ "name": "report.pdf", "data": "<base64>" }]
    }
  }
}
```

Then call `prescan_selected` and `analyze_reports` with the returned names.

### 6.4 Poll pipeline state

```
POST /mcp  →  { "name": "get_state", "arguments": {} }
```

Stages: `extracting` → `unifying` → `rendering` → `completed` / `error`.

### 6.5 RAG (per-student textbook Q&A)

```
POST /mcp  →  { "name": "query_textbook", "arguments": { "textbook_name": "grade10-biology", "question": "What is photosynthesis?", "top_k": "5", "username": "10-A" } }
```

Each student only ever searches their own namespace.

---

## 7. Server-Sent Events (optional, for live progress)

```
GET https://r2p-enterprise.onrender.com/sse
Authorization: Bearer <jwt>
```

Browser `EventSource` cannot set headers, so either use `fetch`-based SSE or pass
`?token=<jwt>`:

```
GET https://r2p-enterprise.onrender.com/sse?token=<jwt>
```

---

## 8. Other useful endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | health check (no auth) |
| GET | `/` | API status page (no auth) |
| GET | `/tools` | list tools as plain JSON (no auth) |
| GET | `/metrics` | server metrics (no auth) |
| POST | `/api/auth/login` | login → JWT |
| POST | `/api/auth/logout` | logout |

---

## 9. Example flow for the frontend

1. **Login screen** → `POST /api/auth/login` → save `access_token`.
2. On first login, call `POST /api/schools/me` to ensure a school is linked.
3. **Dashboard** → `GET /api/schools/me` for stats; students / reports / invoices tabs.
4. **API Keys page** → `GET /api/schools/{id}/keys`, `POST` to create (show once),
   `DELETE` to revoke.
5. **Analysis page** → upload file → `upload_files` → `prescan_selected` →
   `analyze_reports` → poll `get_state` → show results.
6. **RAG chat** → `query_textbook` with the student's `username`.

## 10. CORS

The backend allows requests from origins listed in `CORS_ORIGINS` (comma-separated)
on the Render env. **Give the backend owner your Vercel URL** (e.g.
`https://r2p-frontend.vercel.app`) so it can be added.
