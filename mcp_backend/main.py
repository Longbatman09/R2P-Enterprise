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

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse
import uvicorn

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

    @classmethod
    def get(cls, module_path: str):
        if module_path not in cls._cache:
            try:
                mod = importlib.import_module(module_path)
                cls._cache[module_path] = mod
            except Exception as exc:
                log.warning("Failed to load %s: %s", module_path, exc)
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

# ── in-memory SSE subscribers ─────────────────────────────────────────────────
_subscribers: list[asyncio.Queue] = []

from auth_routes import router as auth_routes
from auth_supabase import get_current_user
app.include_router(auth_routes, prefix="/api/auth", tags=["auth"])
_session_counter = 0


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
async def mcp_endpoint(request: Request, user: dict = Depends(get_current_user)):
    """JSON-RPC over HTTP — the primary MCP entry point. Requires JWT auth."""
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
async def sse_endpoint(user: dict = Depends(get_current_user)):
    """Server-Sent Events stream for MCP push notifications. Requires JWT auth."""
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
async def list_tools_rest():
    """REST GET equivalent of tools/list — useful for dev/debug."""
    return {"tools": [
        {"name": k, **v}
        for k, v in AgentRegistry.all_tools().items()
    ]}

@app.get("/metrics")
async def metrics():
    return {
        "connected_sse_clients": len(_subscribers),
        "registered_tools": len(AgentRegistry.all_tools()),
        "registered_resources": len(AgentRegistry.all_resources()),
    }


# ════════════════════════════════════════════════════════════════════════════════
#  Entrypoint
# ════════════════════════════════════════════════════════════════════════════════


@app.get("/")
async def root():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>R2P MCP Backend Status</title>
        <style>
            body {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                background-color: #0f172a;
                color: #f8fafc;
                margin: 0;
                padding: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }
            .container {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 40px;
                box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
                max-width: 650px;
                width: 90%;
            }
            h1 {
                margin-top: 0;
                background: linear-gradient(to right, #38bdf8, #818cf8);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                font-size: 2.2em;
            }
            h2 {
                color: #e2e8f0;
                border-bottom: 1px solid #334155;
                padding-bottom: 8px;
                margin-top: 30px;
            }
            p {
                line-height: 1.6;
                color: #94a3b8;
            }
            .card {
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 16px;
                margin-top: 16px;
            }
            code {
                background-color: #1e293b;
                padding: 2px 6px;
                border-radius: 4px;
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                color: #38bdf8;
                font-size: 0.9em;
            }
            .code-block {
                display: block;
                padding: 12px;
                margin: 10px 0;
                color: #a7f3d0;
                overflow-x: auto;
            }
            ul {
                line-height: 1.6;
                color: #94a3b8;
                padding-left: 20px;
            }
            li {
                margin-bottom: 8px;
            }
            .badge {
                display: inline-flex;
                align-items: center;
                padding: 4px 12px;
                background-color: rgba(52, 211, 153, 0.1);
                color: #34d399;
                border: 1px solid rgba(52, 211, 153, 0.2);
                border-radius: 9999px;
                font-size: 0.875rem;
                font-weight: 600;
                margin-bottom: 24px;
            }
            .badge::before {
                content: '';
                display: inline-block;
                width: 8px;
                height: 8px;
                background-color: #34d399;
                border-radius: 50%;
                margin-right: 8px;
                box-shadow: 0 0 8px #34d399;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="badge">System Online</div>
            <h1>R2P MCP Server</h1>
            <p>The Model Context Protocol (MCP) backend is running successfully. This server provides AI-powered academic report analysis and tool integrations.</p>
            
            <h2>Environment Configuration</h2>
            <p>To fully utilize the authentication and secure API key storage features, ensure the following environment variables are properly set in your deployment:</p>
            
            <div class="card">
                <ul>
                    <li><code class="code-block">SUPABASE_URL</code> Your Supabase project URL (e.g., https://xyz.supabase.co)</li>
                    <li><code class="code-block">SUPABASE_ANON_KEY</code> The anonymous public key for client requests</li>
                    <li><code class="code-block">SUPABASE_SERVICE_ROLE_KEY</code> The admin key for bypassing RLS during user operations</li>
                    <li><code class="code-block">AES_KEY</code> A 32-byte hex or base64 encoded string used to encrypt third-party API keys at rest. If not set, keys will be stored in base64.</li>
                </ul>
            </div>
            
            <h2>Core Endpoints</h2>
            <ul>
                <li><code>POST /mcp</code> — Primary JSON-RPC endpoint for tool execution</li>
                <li><code>GET /sse</code> — Server-Sent Events stream for asynchronous results</li>
                <li><code>GET /tools</code> — List all registered MCP tools</li>
                <li><code>GET /health</code> — Basic health check and status</li>
            </ul>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


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
