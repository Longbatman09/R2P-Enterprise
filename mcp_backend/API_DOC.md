# R2P MCP Backend — API Reference for Frontend Devs

**Base URL** (replace with your Render URL after deploy)
```
https://r2p-mcp-backend.onrender.com
```

---

## 1. Health & Info

### GET /
No auth required.
```json
{ "message": "R2P MCP backend is running" }
```

### GET /health
No auth required.
```json
{
  "status": "ok",
  "server": "r2p-mcp",
  "fastmcp_mode": true,
  "started_at": "2026-08-06T15:26:32Z"
}
```
Use this to check if the backend is alive before making other calls.

### GET /metrics
No auth required.
```json
{
  "connected_sse_clients": 0,
  "registered_tools": 0,
  "registered_resources": 0
}
```
Useful for debugging — tells you how many tools the backend has loaded.

---

## 2. MCP Tool Calling (the core)

### POST /mcp
**Auth required** — header: `Authorization: Bearer <jwt>`
**Content-Type:** `application/json`

This is the single endpoint for all MCP operations (list tools, call a tool).

#### A. List all available tools
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list"
}
```
Response — array of tools with `name`, `description`, `inputSchema`:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "ingest_textbook",
        "description": "Ingest a textbook PDF into the RAG index.",
        "inputSchema": {
          "type": "object",
          "properties": { "pdf_path": {"type": "string"} },
          "required": ["pdf_path"]
        }
      },
      {
        "name": "query_textbook",
        "description": "Ask a question against the indexed textbook.",
        "inputSchema": { ... }
      }
    ]
  }
}
```

#### B. Call a tool
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "query_textbook",
    "arguments": { "question": "What is photosynthesis?" }
  }
}
```
Response:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [{ "type": "text", "text": "Photosynthesis is... ..." }]
  }
}
```

#### Error response
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "error": {
    "code": -32601,
    "message": "Tool 'xxx' not found."
  }
}
```

---

## 3. SSE (Server-Sent Events)

### GET /sse
**Auth required** — header: `Authorization: Bearer <jwt>`

Returns a streaming SSE connection. The backend pushes tool-call results here.
Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`

Events look like:
```
data: {"jsonrpc":"2.0","id":2,"result":{"content":[...]}}
```

Frontend should use `EventSource`:
```javascript
const evtSource = new EventSource(
  "https://<render-url>/sse",
  { headers: { Authorization: `Bearer ${jwt}` } }
);
evtSource.onmessage = (e) => console.log("tool result:", JSON.parse(e.data));
```

---

## 4. Auth & API Key Management

All below endpoints are under `/api/auth` prefix.

### POST /api/auth/signup
No auth required. Creates a new account.
```json
{ "email": "user@example.com", "password": "pass123", "username": "kabilan" }
```

### POST /api/auth/login
No auth required. Returns JWT.
```json
{ "email": "user@example.com", "password": "pass123" }
```
Response:
```json
{
  "access_token": "<supabase_jwt>",
  "refresh_token": "<refresh>",
  "user": { "id": "...", "email": "user@example.com" }
}
```
**Store `access_token` in localStorage or a cookie and send as:**
```
Authorization: Bearer <access_token>
```

### POST /api/auth/logout
Auth required. Logs out the current user.

### GET /api/auth/keys
Auth required. Lists all stored API keys (masks the actual value).

### GET /api/auth/keys/{service}
Auth required. Get a specific key.
Services: `supabase`, `pinecone`, `nvidia_nim`, `nvidia`, `gemini`, `llmwhisperer`, `firebase`

### PUT /api/auth/keys/{service}
Auth required. Save or update an API key.
```json
{ "service": "nvidia_nim", "key": "nvapi-xxxx-xxxx-xxxx" }
```

### DELETE /api/auth/keys/{service}
Auth required. Delete a stored API key.

---

## 5. Registered MCP Server Modules

The backend loads these modules and exposes all public functions with docstrings as tools:

| Module | Purpose |
|---|---|
| `mcp_servers.rag_system` | Textbook PDF ingestion + RAG query |
| `mcp_servers.vision_extractor` | OCR / diagram extraction from PDFs |
| `mcp_servers.plot_renderer` | Matplotlib/chart rendering |
| `mcp_servers.file_watcher` | File system monitoring |
| `agents.llmwhisperer_converter` | NVIDIA LLMWhisperer file conversion |
| `agents.local_mem` | Local memory / context store |

To see what tools are available from each module, call:
```bash
POST /mcp  with  { "method": "tools/list" }
```

---

## 6. CORS

The backend accepts requests from:
```
https://r2p-frontend.vercel.app
```
(Configurable via `CORS_ORIGINS` env var on Render — comma-separated list.)

---

## 7. Quick Start for Frontend

```javascript
// 1. Login
const login = async (email, password) => {
  const res = await fetch("https://<render-url>/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  const data = await res.json();
  localStorage.setItem("jwt", data.access_token);
  return data.access_token;
};

// 2. List tools
const listTools = async (jwt) => {
  const res = await fetch("https://<render-url>/mcp", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${jwt}`
    },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" })
  });
  return res.json();
};

// 3. Call a tool
const callTool = async (jwt, toolName, args) => {
  const res = await fetch("https://<render-url>/mcp", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${jwt}`
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: Date.now(),
      method: "tools/call",
      params: { name: toolName, arguments: args }
    })
  });
  return res.json();
};

// 4. SSE stream (for long-running tool results)
const subscribeSSE = (jwt) => {
  const evtSource = new EventSource(
    `https://<render-url>/sse`,
    { headers: { Authorization: `Bearer ${jwt}` } }
  );
  evtSource.onmessage = (e) => console.log("Result:", JSON.parse(e.data));
  return evtSource;
};
```
