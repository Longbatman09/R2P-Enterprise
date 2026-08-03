"""
RAG MCP Server — NVIDIA NIM + Pinecone powered textbook learning assistant.

PDF text extraction: Node.js subprocess → pdf-parse (pure JS, no C deps).
Pinecone:            Direct REST API (no SDK pip install needed).
Embeddings / Chat:   NVIDIA NIM REST API.

Pipeline:
  1. Upload PDF → Node.js pdf-parse extracts per-page text
  2. Chunk text (~4 KB, 200-char overlap)
  3. Embed with NVIDIA NIM (nemotron-3-embed-1b)
  4. Upsert to Pinecone (serverless index, cosine metric)
  5. Query → embed question → Pinecone search → NIM answer with citations

MCP tools:
  ingest_textbook   upload+embed a textbook PDF
  query_textbook    ask a question against a textbook
  list_textbooks    list all ingested textbooks
  delete_textbook   remove a textbook from Pinecone
  health_check      sanity-check Pinecone + NIM
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("rag_system")

mcp = FastMCP("rag-system")

# ─── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).resolve().parent.parent
RAG_DATA_DIR    = PROJECT_ROOT / "rag_data"
RAG_DATA_DIR.mkdir(parents=True, exist_ok=True)

EXTRACTOR_SCRIPT = Path(__file__).resolve().parent / "pdf_extractor.js"
NODE_MODULES     = Path("/tmp/node_modules")

# ─── Credentials ──────────────────────────────────────────────────────────────
PINECONE_API_KEY      = os.environ.get("PINECONE_API_KEY", "")
NVIDIA_API_KEY        = os.environ.get("NVIDIA_API_KEY", "")
PINECONE_INDEX_PREFIX = "textbook-"
EMBED_DIM             = 2048

NIM_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
NIM_CHAT_URL  = "https://integrate.api.nvidia.com/v1/chat/completions"
EMBED_MODEL   = "nvidia/nemotron-3-embed-1b"
CHAT_MODEL    = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

CHUNK_CHAR_LIMIT = 4000
CHUNK_OVERLAP    = 200

# ─── Pinecone REST helpers ────────────────────────────────────────────────────
PC_REST = "https://api.pinecone.io"


def _pc_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Api-Key":       PINECONE_API_KEY,
    }


def _pc_list_indexes() -> list[str]:
    r = requests.get(f"{PC_REST}/indexes", headers=_pc_headers(), timeout=15)
    r.raise_for_status()
    data = r.json()
    return [i["name"] for i in data.get("indexes", [])]


def _pc_create_index(name: str) -> None:
    payload = {
        "name":     name,
        "dimension": EMBED_DIM,
        "metric":   "cosine",
        "spec":     {"serverless": {"cloud": "aws", "region": "us-east-1"}},
    }
    r = requests.post(f"{PC_REST}/indexes", headers=_pc_headers(), json=payload, timeout=30)
    r.raise_for_status()
    time.sleep(10)  # Wait longer for serverless index


def _pc_get_host(index_name: str) -> str:
    url = f"{PC_REST}/indexes/{index_name}"
    r = requests.get(url, headers=_pc_headers(), timeout=15)
    r.raise_for_status()
    return r.json().get("host", "")



def _pc_upsert(index_name: str, vectors: list[dict]) -> None:
    """Upsert a batch of vectors (max 100 per call)."""
    host = _pc_get_host(index_name)
    url = f"https://{host}/vectors/upsert"
    r = requests.post(url, headers=_pc_headers(), json={"vectors": vectors, "namespace": ""}, timeout=60)
    r.raise_for_status()


def _pc_query(index_name: str, vector: list[float], top_k: int = 4) -> list[dict]:
    host = _pc_get_host(index_name)
    url = f"https://{host}/query"
    payload = {
        "vector":        vector,
        "topK":          top_k,
        "includeValues": False,
        "includeMetadata": True,
    }
    r = requests.post(url, headers=_pc_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json().get("matches", [])


def _pc_delete_index(index_name: str) -> None:
    url = f"{PC_REST}/indexes/{index_name}"
    try:
        r = requests.delete(url, headers=_pc_headers(), timeout=30)
        if r.status_code == 404:
            return  # already gone, treat as success
        r.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"Pinecone delete failed for {index_name}: {exc}") from exc


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def _require_env(key: str) -> str:
    val = os.environ.get(key, "")
    _require(bool(val), f"{key} not set in environment.")
    return val


def _find_node() -> str:
    node = shutil.which("node")
    _require(node is not None, "Node.js not found in PATH.")
    return node


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:40]


def _idx_name(textbook_id: str) -> str:
    return f"{PINECONE_INDEX_PREFIX}{_normalize(textbook_id)}"


def _ensure_index(textbook_id: str) -> str:
    name = _idx_name(textbook_id)
    try:
        existing = _pc_list_indexes()
    except Exception as exc:
        raise RuntimeError(f"Pinecone list-indexes failed: {exc}")
    if name not in existing:
        _pc_create_index(name)
    return name


def _extract_pdf(pdf_bytes: bytes, timeout: int = 60) -> str:
    node = _find_node()
    b64  = base64.b64encode(pdf_bytes).decode()
    if not EXTRACTOR_SCRIPT.exists():
        logger.warning("pdf_extractor.js missing — returning empty text.")
        return ""
    try:
        proc = subprocess.run(
            [node, str(EXTRACTOR_SCRIPT), b64],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            logger.error("extractor stderr: %s", proc.stderr[:400])
            return ""
        pages = json.loads(proc.stdout.strip())
        return "\n\n".join(p.get("text", "") for p in pages if p.get("text", "").strip())
    except Exception as exc:
        logger.error("PDF extraction failed: %s", exc)
        return ""


def _chunk_text(text: str, limit: int = CHUNK_CHAR_LIMIT, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks, pos = [], 0
    while pos < len(text):
        end = pos + limit
        if end >= len(text):
            chunks.append(text[pos:])
            break
        boundary = text.rfind("\n\n", pos + overlap, end)
        if boundary == -1 or boundary <= pos + overlap:
            boundary = end
        chunks.append(text[pos:boundary])
        pos = max(pos + 1, boundary - overlap)
    return chunks


def _build_chunks(pages: list[dict], textbook_name: str) -> list[dict]:
    all_chunks: list[dict] = []
    for page in pages:
        for i, chunk_text in enumerate(_chunk_text(page.get("text", ""))):
            cid = (
                f"{_normalize(textbook_name)}-p{page['page']}"
                f"-{abs(hash(chunk_text)) & 0xFFFFFFFF:08x}"
            )
            all_chunks.append({
                "id":       cid,
                "text":     chunk_text,
                "page":     page["page"],
                "textbook": textbook_name,
            })
    return all_chunks


# ─── NVIDIA NIM ───────────────────────────────────────────────────────────────

def _nim_embed(texts: list[str], input_type: str = "passage") -> list[list[float]]:
    _require_env("NVIDIA_API_KEY")
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
    }
    payload = {"model": EMBED_MODEL, "input": texts, "input_type": input_type}
    r = requests.post(NIM_EMBED_URL, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return [d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"])]


def _nim_chat(messages: list[dict], max_tokens: int = 600) -> str:
    _require_env("NVIDIA_API_KEY")
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
    }
    payload = {"model": CHAT_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3}
    r = requests.post(NIM_CHAT_URL, headers=headers, json=payload, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


# ═════════════════════════════════════════════════════════════════════════════
#  MCP Tools
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def ingest_textbook(file_bytes_b64: str, textbook_name: str,
                    file_name: str = "textbook.pdf") -> dict:
    """
    Upload a textbook PDF, extract text, chunk, embed via NVIDIA NIM,
    and store vectors in Pinecone.

    Parameters:
      file_bytes_b64  Base64-encoded PDF file content
      textbook_name   Human-readable textbook name (used as index slug)
      file_name       Original filename (for logging)
    """
    _require_env("PINECONE_API_KEY")
    _require_env("NVIDIA_API_KEY")

    try:
        pdf_bytes = base64.b64decode(file_bytes_b64)
    except Exception as exc:
        return {"status": "error", "error": f"Invalid base64: {exc}"}

    tex_id   = textbook_name.strip()
    idx_name = _ensure_index(tex_id)

    # 1 — Extract text
    full_text = _extract_pdf(pdf_bytes)
    if not full_text:
        return {"status": "error",
                "error": "No text extracted. The PDF may be scanned/image-only."}

    pages = [{"page": 1, "text": full_text}]

    # 2 — Chunk
    chunks = _build_chunks(pages, tex_id)
    if not chunks:
        return {"status": "error", "error": "PDF produced zero chunks."}

    # 3 — Embed + upsert in batches of 64
    BATCH = 64
    upserted = 0

    for start in range(0, len(chunks), BATCH):
        batch = chunks[start: start + BATCH]
        vectors: list[dict] = []
        try:
            embeddings = _nim_embed([c["text"] for c in batch])
        except Exception as exc:
            return {"status": "error",
                    "error": f"NIM embed failed at chunk {start}: {exc}",
                    "upserted_so_far": upserted}

        for chunk, emb in zip(batch, embeddings):
            vectors.append({
                "id":       chunk["id"],
                "values":   emb,
                "metadata": {
                    "textbook": chunk["textbook"],
                    "page":     chunk["page"],
                    "text":     chunk["text"][:4000],
                },
            })

        try:
            _pc_upsert(idx_name, vectors)
            upserted += len(vectors)
        except Exception as exc:
            return {"status": "error",
                    "error": f"Pinecone upsert failed: {exc}",
                    "upserted_so_far": upserted}

    # 4 — Local manifest
    manifest = {
        "textbook_name":  tex_id,
        "pinecone_index": idx_name,
        "n_pages":        1,
        "total_chars":    len(full_text),
        "n_chunks":       len(chunks),
        "upserted":       upserted,
        "embed_model":    EMBED_MODEL,
        "file_name":      file_name,
        "created_at":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (RAG_DATA_DIR / f"{idx_name}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    return {
        "status":         "success",
        "textbook_name":  tex_id,
        "pinecone_index": idx_name,
        "n_pages":        1,
        "total_chars":    len(full_text),
        "n_chunks":       len(chunks),
        "upserted":       upserted,
    }


@mcp.tool()
def query_textbook(textbook_name: str, question: str, top_k: int = 4, username: str = None) -> dict:
    """
    RAG query: embed question → Pinecone search → NIM answer with citations.

    Parameters:
      textbook_name  Name given during ingest
      question       Student's natural-language question
      top_k          Number of chunks to retrieve (default 4)
      username       Name of the logged-in student (optional)
    """
    _require_env("NVIDIA_API_KEY")
    _require_env("PINECONE_API_KEY")

    if not username:
        session_file = PROJECT_ROOT / "session.json"
        if session_file.exists():
            try:
                session_data = json.loads(session_file.read_text(encoding="utf-8"))
                username = session_data.get("username")
            except Exception:
                pass

    search_query = question
    # Check if the query has first-person pronouns
    if username and re.search(r"\b(i|me|my|myself|we|us|our|ours)\b", question, re.IGNORECASE):
        search_query = f"{username} {question}"

    try:
        q_vec = _nim_embed([search_query], input_type="query")[0]
    except Exception as exc:
        return {"status": "error", "error": f"NIM embed error: {exc}"}

    try:
        if textbook_name.lower() == "all":
            all_tbs = list_textbooks().get("textbooks", [])
            matches = []
            for tb in all_tbs:
                try:
                    tb_idx = tb["pinecone_index"]
                    idx_matches = _pc_query(tb_idx, q_vec, top_k=top_k)
                    matches.extend(idx_matches)
                except Exception as exc:
                    logger.error("Failed to query %s: %s", tb_idx, exc)
            
            # Sort by score and keep top_k
            matches = sorted(matches, key=lambda x: float(x.get("score", 0.0)), reverse=True)[:top_k]
        else:
            matches = _pc_query(_idx_name(textbook_name), q_vec, top_k=top_k)
    except Exception as exc:
        return {"status": "error", "error": f"Pinecone query error: {exc}"}

    if not matches:
        return {
            "status":  "ok",
            "answer":  ("I couldn't find relevant content for this question. "
                        "Try rephrasing or selecting a different textbook."),
            "sources": [],
        }

    context_blocks: list[str] = []
    sources: list[dict] = []

    for i, match in enumerate(matches, start=1):
        meta  = match.get("metadata", {})
        text  = meta.get("text", "").strip()
        score = round(float(match.get("score", 0.0)), 4)
        context_blocks.append(f"[Source {i} — page {meta.get('page', '?')}]\n{text}")
        sources.append({
            "page":         meta.get("page", "?"),
            "text_snippet": text[:300] + ("…" if len(text) > 300 else ""),
            "score":        score,
        })

    context = "\n\n".join(context_blocks)

    system_prompt = (
        "You are an expert academic tutor. "
        "Answer using ONLY the context passages provided. "
        "If the answer is absent, say 'The provided textbook does not cover this topic.' "
        "Always cite source numbers."
    )
    if username:
        system_prompt += f" The current user asking the question is named '{username}' (referred to as 'I', 'me', 'my', 'myself' in the question)."
    user_prompt = (
        f"Question: {question}\n\n"
        f"Textbook context:\n{context}\n\n"
        "Provide a clear, concise answer with citations."
    )

    try:
        answer = _nim_chat([
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ], max_tokens=600)
    except Exception as exc:
        return {"status": "error", "error": f"NIM chat error: {exc}"}

    return {"status": "ok", "answer": answer, "sources": sources}


@mcp.tool()
def list_textbooks() -> dict:
    """List all ingested textbooks from local manifests."""
    manifests: list[dict] = []
    try:
        for fp in sorted(RAG_DATA_DIR.glob("textbook-*.json"),
                         key=lambda p: p.stat().st_mtime,
                         reverse=True):
            manifests.append(json.loads(fp.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.error("scan failed: %s", exc)
    return {"textbooks": manifests, "count": len(manifests)}


@mcp.tool()
def delete_textbook(textbook_name: str) -> dict:
    """Delete a textbook index from Pinecone and remove the local manifest."""
    name = _idx_name(textbook_name.strip())
    try:
        _pc_delete_index(name)
    except Exception as exc:
        return {"status": "error", "error": f"Pinecone delete error: {exc}"}
    manifest = RAG_DATA_DIR / f"{name}.json"
    if manifest.exists():
        manifest.unlink()
    return {"status": "success", "deleted_index": name}


@mcp.tool()
def health_check() -> dict:
    """Probe Pinecone + NVIDIA NIM connectivity."""
    checks: dict[str, Any] = {}

    try:
        checks["pinecone"] = {"status": "ok", "indexes": _pc_list_indexes()}
    except Exception as exc:
        checks["pinecone"] = {"status": "error", "error": str(exc)}

    try:
        emb = _nim_embed(["health"])
        checks["nim_embed"] = {
            "status": "ok", "model": EMBED_MODEL, "dim": len(emb[0]),
        }
    except Exception as exc:
        checks["nim_embed"] = {"status": "error", "error": str(exc)}

    if checks.get("nim_embed", {}).get("status") == "ok":
        try:
            r = _nim_chat([{"role": "user", "content": "Say RAG-OK"}], max_tokens=8)
            checks["nim_chat"] = {"status": "ok", "model": CHAT_MODEL, "reply": r[:60]}
        except Exception as exc:
            checks["nim_chat"] = {"status": "error", "error": str(exc)}

    overall = "ok" if all(v.get("status") == "ok" for v in checks.values()) else "degraded"
    return {"overall": overall, "checks": checks}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mcp.run()
