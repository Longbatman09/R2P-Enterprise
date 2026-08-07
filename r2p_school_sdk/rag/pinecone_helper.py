"""
Tiny helper to get an embedding from NVIDIA NIM before calling the
PerStudentRAGManager.  Kept separate so the backend (rag_system.py) can
import and reuse it from the MCP /mcp handler.
"""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("r2p.rag")


def embed_query(text: str, model: str | None = None, *, timeout: int = 30) -> list[float]:
    model = model or os.environ.get(
        "NVIDIA_EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5"
    )
    api_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY", "")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY not configured — cannot embed query")

    url = "https://integrate.api.nvidia.com/v1/embeddings"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"input": [text], "model": model},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    return data["data"][0]["embedding"]
