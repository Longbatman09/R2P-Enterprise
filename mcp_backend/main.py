"""R2P-Enterprise MCP Server — FastAPI + SSE
Implements the Model Context Protocol (JSON-RPC 2.0 over HTTP/SSE).

MCP Methods exposed
-------------------
tools/list              — list all available tools
tools/call              — invoke a tool by name
resources/list          — list static resources (schemas, configs)
logging/setLevel        — control server log verbosity

Client uses SSE endpoint to receive tool-call results;
POST /mcp is used for all JSON-RPC calls.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse
import uvicorn

from auth_supabase import get_current_user

# ── project bootstrap ────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── environment ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("mcp_server")

# ── lazy agent imports ────────────────────────────────────────────────────────
def _lazy(module_path: str, attr: str = None):
    """Import a module (optionally fetch an attribute) and cache it."""
    mod = importlib.import_module(module_path)
    return getattr(mod, attr) if attr else mod


class AgentRegistry:
    """Lazy-loads all MCP-tool modules; each module must expose
    a `tools` dict mapping tool-name → callable, and a `resources` list."""

    # (module_path, attr_to_export)
    _SERVICES = [
        ("mcp_servers.rag_system", None),
        ("mcp_servers.vision_extractor", None),
        ("mcp_servers.plot_renderer", None),
        ("mcp_servers.file_watcher", None),
        ("mcp_servers.ui_orchestrator", None),
        ("agents.llmwhisperer_converter", "convert_file"),
        ("agents.local_mem", None),
    ]

    _cache: dict[str, Any] = {}
    _load_errors: dict[str, str] = {}

    @classmethod
    def get(cls, module_path: str):
        if module_path not in cls._cache:
            try:
                mod = importlib.import_module(module_path)
                cls._cache[module_path] = mod
            except Exception as exc:
                log.warning("Failed to load %s: %s", module_path, exc)
                cls._load_errors[module_path] = f"{type(exc).__name__}: {exc}"
                cls._cache[module_path] = None
        return cls._cache[module_path]

    @classmethod
    def all_tools(cls) -> dict[str, dict]:
        import inspect
        result = {}
        for module_path, _ in cls._SERVICES:
            mod = cls.get(module_path)
            if mod is None:
                continue
            for name, obj in vars(mod).items():
                if (
                    inspect.isroutine(obj)
                    and not name.startswith("_")
                    and obj.__doc__
                ):
                    try:
                        sig = inspect.signature(obj)
                    except ValueError:
                        continue
                    props: dict = {}
                    params: list = []
                    for p_name, p in sig.parameters.items():
                        props[p_name] = {"type": "string"}
                        params.append({
                            "name": p_name,
                            "description": obj.__doc__.strip().split("\n")[0],
                            "type": "string",
                        })
                    if sig.return_annotation is not inspect.Signature.empty:
                        props["return"] = {"type": "string"}
                    result[name] = {
                        "name": name,
                        "description": obj.__doc__.strip().split("\n")[0],
                        "inputSchema": {
                            "type": "object",
                            "properties": props,
                            "required": [p["name"] for p in params],
                        },
                    }
        return result

    @classmethod
    def all_resources(cls) -> list[dict]:
        resources = []
        for module_path, _ in cls._SERVICES:
            mod = cls.get(module_path)
            if mod is None or not hasattr(mod, "resources"):
                continue
            for r in mod.resources:
                resources.append(r)
        return resources

    @classmethod
    def call_tool(cls, name: str, arguments: dict) -> Any:
        for module_path, _ in cls._SERVICES:
            mod = cls.get(module_path)
            if mod is None:
                continue
            fn = getattr(mod, name, None)
            if callable(fn):
                return fn(**arguments)
        raise ValueError(f"Tool '{name}' not found.")


# ── compatibility shim: if fastmcp isn't installed, patch the modules ─────────
try:
    from fastmcp import FastMCP  # type: ignore
    _FASTMCP_AVAILABLE = True
except ImportError:
    _FASTMCP_AVAILABLE = False
    log.warning("fastmcp not installed — running with compatibility shim")

    # We still need to import our modules; their mcp guard handles the absence
    # of FastMCP (see vision_extractor._NullServer pattern).


# ════════════════════════════════════════════════════════════════════════════════
#  FastAPI application
# ════════════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="R2P-Enterprise MCP Server",
    description="AI-powered academic report analysis via Model Context Protocol",
    version="1.0.0",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# The web frontend (Vercel) must be allowed to call this API from the browser.
# Origins come from the CORS_ORIGINS env var (comma-separated). If unset,
# fall back to the project's known frontend domains so the deployed web app
# keeps working even before env vars are configured on the host.
from fastapi.middleware.cors import CORSMiddleware

DEFAULT_CORS_ORIGINS = [
    "https://r2p-enterprise.vercel.app",
    "https://r2p-frontend.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://localhost:8899",
]

_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "").split(",")
    if o.strip()
] or DEFAULT_CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
log.info(
    "CORS enabled for origins: %s", _cors_origins
)

# ── in-memory SSE subscribers ─────────────────────────────────────────────────
_subscribers: list[asyncio.Queue] = []

from auth_routes import router as auth_routes
from auth_supabase import get_current_user
app.include_router(auth_routes, prefix="/api/auth", tags=["auth"])
from school_routes import router as school_routes
app.include_router(school_routes, prefix="/api/schools", tags=["schools"])
_session_counter = 0

# ── unified auth: Supabase JWT (frontend) OR school API key (SDK) ──────────────
_bearer_scheme = HTTPBearer(auto_error=False)


def _school_key_hash(plain: str) -> str:
    import hashlib
    return hashlib.sha256(plain.encode()).hexdigest()


async def get_api_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    """Resolve the caller as either a logged-in user (Supabase JWT) or a
    school integration key (SDK, sk_...). Returns a dict with `kind`:

      {"kind": "user",   "user": {...}}       – frontend / dashboard
      {"kind": "school", "school_id": ..., "key": {...}} – SDK integration

    The token is read from the Authorization header, or from ?token= for
    clients that cannot set headers (e.g. browser EventSource).
    """
    token = credentials.credentials if credentials else None
    if not token:
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if token.startswith("sk_"):
        from database import find_active_key
        key_row = find_active_key(_school_key_hash(token))
        if not key_row:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        return {
            "kind": "school",
            "school_id": key_row.get("school_id"),
            "key": key_row,
        }

    user = await get_current_user(credentials)
    return {"kind": "user", "user": user}


def _broadcast(event: dict):
    for q in list(_subscribers):
        q.put_nowait(event)


async def _sse_stream():
    """Yield events until client disconnects."""
    global _session_counter
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.append(queue)
    _session_counter += 1
    session_id = _session_counter
    log.info("SSE client connected [session=%d]", session_id)
    try:
        while True:
            data = await queue.get()
            yield {"event": "message", "data": json.dumps(data)}
    finally:
        with contextlib.suppress(ValueError):
            _subscribers.remove(queue)
        log.info("SSE client disconnected [session=%d]", session_id)


# ════════════════════════════════════════════════════════════════════════════════
#  MCP — JSON-RPC 2.0 handler
# ════════════════════════════════════════════════════════════════════════════════
def _handle_mcp_request(body: dict, session_id: str) -> dict:
    """Dispatch a single JSON-RPC 2.0 request and return a response dict."""
    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    log.debug("MCP method=%s id=%s", method, req_id)

    # ---- initialize ──────────────────────────────────────────────────────────
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {
                "name": "r2p-enterprise",
                "version": "1.0.0",
            },
        }
        return _jsonrpc_success(req_id, result)

    # ---- tools/list ──────────────────────────────────────────────────────────
    if method == "tools/list":
        tools = []
        for name, meta in AgentRegistry.all_tools().items():
            tools.append({
                "name": name,
                "description": meta.get("description", ""),
                "inputSchema": meta.get("inputSchema", {"type": "object"}),
            })
        return _jsonrpc_success(req_id, {"tools": tools})

    # ---- tools/call ──────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        try:
            raw = AgentRegistry.call_tool(tool_name, arguments)
            return _jsonrpc_success(req_id, {
                "content": [{"type": "text", "text": json.dumps(raw)}],
            })
        except Exception as exc:
            return _jsonrpc_error(req_id, -1, str(exc))

    # ---- resources/list ──────────────────────────────────────────────────────
    if method == "resources/list":
        return _jsonrpc_success(req_id, {
            "resources": AgentRegistry.all_resources(),
        })

    # ---- logging/setLevel ────────────────────────────────────────────────────
    if method == "logging/setLevel":
        level = getattr(logging, params.get("level", "INFO").upper(), logging.INFO)
        logging.getLogger().setLevel(level)
        return _jsonrpc_success(req_id, {})

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")


def _jsonrpc_success(req_id, result):
    resp = {"jsonrpc": "2.0", "result": result}
    if req_id is not None:
        resp["id"] = req_id
    return resp


def _jsonrpc_error(req_id, code, message):
    resp = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if req_id is not None:
        resp["id"] = req_id
    return resp


@app.post("/mcp")
async def mcp_endpoint(request: Request, principal: dict = Depends(get_api_principal)):
    """JSON-RPC over HTTP — the primary MCP entry point.
    Accepts either a Supabase JWT (frontend) or a school integration key (SDK)."""
    session_id = str(id(request))
    body = await request.json()

    # Handle batched requests
    if isinstance(body, list):
        responses = [_handle_mcp_request(r, session_id) for r in body]
        return JSONResponse(responses)

    response = _handle_mcp_request(body, session_id)
    # Notify SSE subscribers in background
    asyncio.get_event_loop().run_in_executor(
        None, _broadcast, {"type": "mcp_response", "session": session_id, "data": response}
    )
    return JSONResponse(response)


@app.get("/sse")
async def sse_endpoint(principal: dict = Depends(get_api_principal)):
    """Server-Sent Events stream for MCP push notifications.
    Accepts either a Supabase JWT (frontend) or a school integration key (SDK)."""
    return EventSourceResponse(_sse_stream())


# ════════════════════════════════════════════════════════════════════════════════
#  Health / readiness
# ════════════════════════════════════════════════════════════════════════════════
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "server": "r2p-mcp",
        "fastmcp_mode": _FASTMCP_AVAILABLE,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.get("/tools")
async def list_tools_rest(principal: dict = Depends(get_api_principal)):
    """REST GET equivalent of tools/list — requires a valid key or JWT."""
    return {"tools": [
        {"name": k, **v}
        for k, v in AgentRegistry.all_tools().items()
    ]}

@app.get("/metrics")
async def metrics(principal: dict = Depends(get_api_principal)):
    """Internal diagnostics — requires a valid key or JWT."""
    return {
        "connected_sse_clients": len(_subscribers),
        "registered_tools": len(AgentRegistry.all_tools()),
        "registered_resources": len(AgentRegistry.all_resources()),
        "module_load_errors": AgentRegistry._load_errors,
    }


# ── generic 500s — real error details stay in server logs only ──────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error(
        "Unhandled error on %s %s", request.method, request.url.path,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ════════════════════════════════════════════════════════════════════════════════
#  Entrypoint
# ════════════════════════════════════════════════════════════════════════════════


@app.get("/")
async def root():
    """API status page. The frontend is a separate repo; this endpoint
    only confirms the backend is up and points to the docs."""
    return {
        "name": "R2P-Enterprise MCP Backend",
        "status": "running",
        "version": "1.0.0",
        "docs": "/docs",
        "api_doc": "https://github.com/Longbatman09/R2P-Enterprise/blob/main/mcp_backend/API_DOC.md",
        "frontend_integration_guide": "https://github.com/Longbatman09/R2P-Enterprise/blob/main/Documentations/FRONTEND_INTEGRATION.md",
        "endpoints": {
            "health": "/health",
            "login": "/api/auth/login",
            "school": "/api/schools/me",
            "mcp": "/mcp",
            "sse": "/sse",
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8100)))
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    args = parser.parse_args()

    # Warm up agent modules (lazy import)
    AgentRegistry.all_tools()
    log.info(
        "R2P MCP Server ready — %d tools, %d resources on http://%s:%d",
        len(AgentRegistry.all_tools()),
        len(AgentRegistry.all_resources()),
        args.host,
        args.port,
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
