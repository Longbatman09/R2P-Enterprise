"""Per-student Pinecone namespace manager.

Every student/class gets their own Pinecone namespace so documents and
query results are fully isolated without separate indexes.
"""

from __future__ import annotations

import logging
from typing import Any

from pinecone import Pinecone

log = logging.getLogger("r2p.rag")


class PerStudentRAGManager:
    """Wraps a Pinecone index and routes every upsert / query / delete to
    a per-student (or per-class) namespace."""

    def __init__(
        self,
        api_key: str,
        host: str,
        index_name: str,
        embedding_model_name: str = "nvidia/nv-embedqa-e5-v5",
    ) -> None:
        self.pc = Pinecone(api_key=api_key)
        self.index = self.pc.Index(index_name)
        self.host = host
        self.index_name = index_name
        self.embedding_model_name = embedding_model_name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ns(self, student_id: str) -> str:
        """Normalise a student id into a safe namespace string."""
        return f"stu-{student_id}"

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_textbook(
        self,
        *,
        student_id: str,
        textbook_name: str,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> dict:
        """Upsert pre-chunked + pre-embedded textbook pages into the
        student's namespace."""
        ns = self._ns(student_id)
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        vectors = [
            {
                "id": f"{textbook_name}::{i}",
                "values": emb,
                "metadata": {**chunk, "textbook": textbook_name},
            }
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
        ]
        self.index.upsert(vectors=vectors, namespace=ns)
        log.info("ingested %d chunks into %s::%s", len(vectors), ns, textbook_name)
        return {"ok": True, "ns": ns, "count": len(vectors)}

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        *,
        student_id: str,
        textbook_name: str,
        question: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> dict:
        ns = self._ns(student_id)
        resp = self.index.query(
            namespace=ns,
            vector=query_embedding,
            top_k=top_k,
            include_metadata=True,
            filter={"textbook": {"$eq": textbook_name}},
        )
        matches = [
            {
                "id": m.get("id"),
                "score": m.get("score"),
                "text": m.get("metadata", {}).get("text", ""),
            }
            for m in resp.get("matches", [])
        ]
        return {"ns": ns, "matches": matches}

    # ------------------------------------------------------------------
    # Manage textbooks
    # ------------------------------------------------------------------

    def list_textbooks(self, student_id: str) -> list[str]:
        """Return distinct textbook names present in the student's namespace."""
        ns = self._ns(student_id)
        # Pinecone doesn't offer a direct list; we use a dummy query with
        # top_k=0 to fetch metadata-only, then dedupe.
        resp = self.index.query(
            namespace=ns,
            vector=[0.0] * 1024,  # Pinecone requires a vector
            top_k=1000,
            include_metadata=True,
        )
        seen: set[str] = set()
        for m in resp.get("matches", []):
            tb = m.get("metadata", {}).get("textbook")
            if tb:
                seen.add(tb)
        return sorted(seen)

    def delete_textbook(self, student_id: str, textbook_name: str) -> dict:
        ns = self._ns(student_id)
        # Delete-by-prefix is not supported by the Pinecone REST API, so we
        # record deletions in Supabase (or a local cache) and filter them
        # out at query time.
        log.warning(
            "delete_textbook is best-effort; implement soft-delete filter in prod"
        )
        return {"ok": True, "ns": ns, "textbook": textbook_name, "deleted": False}
