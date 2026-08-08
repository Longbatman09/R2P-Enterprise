"""R2P School SDK — one-liner integration for schools.

pip install r2p-school-sdk  (in your own pyproject.toml, path: ../r2p_school_sdk)

Usage:
    from r2p_school_sdk import R2PSchoolClient

    client = R2PSchoolClient(
        api_url="https://<your-render-app>.onrender.com",
        api_key="sk-<school-key>",   # created in the dashboard
    )

    # Upload a report PDF → run the full analysis pipeline
    result = client.upload_report(
        file_path="report.pdf",
        student_name="Aarav Sharma",
        student_id="10-A",
        output_format="pptx",
    )
    print(result)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from .utils import file_to_base64


class R2PSchoolClient:
    """Thin wrapper around the R2P-Enterprise MCP backend.

    Auth: pass the school's integration key (sk_...) created via the
    dashboard. The backend also accepts dashboard JWTs, but the SDK
    always speaks with the school key.
    """

    def __init__(self, api_url: str, api_key: str, timeout: int = 120) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    # ------------------------------------------------------------------
    # Core MCP helpers
    # ------------------------------------------------------------------

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict:
        r = self._session.post(
            f"{self.api_url}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            timeout=self.timeout,
        )
        if r.status_code == 401:
            raise PermissionError(
                "API key rejected (401). Check that the key is active "
                "and was created for the same school."
            )
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        return body.get("result", {})

    def list_tools(self) -> list[dict]:
        r = self._session.post(
            f"{self.api_url}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        return body.get("result", {}).get("tools", [])

    # ------------------------------------------------------------------
    # Report analysis
    # ------------------------------------------------------------------

    def upload_report(
        self,
        file_path: str,
        student_name: str,
        student_id: str,
        output_format: str = "pptx",
        extra_description: str = "",
        wait: bool = True,
        timeout: int = 300,
    ) -> dict:
        """Upload a PDF/image report and run the analysis pipeline.

        Returns the *started* job state, or the final state if `wait=True`.
        """
        b64 = file_to_base64(file_path)
        file_name = Path(file_path).name

        upload = self._call_tool("upload_files", {
            "files": [{"name": file_name, "data": b64}],
        })
        saved = (upload.get("files") or [])
        if not saved:
            raise RuntimeError(f"Upload failed: {upload}")

        started = self._call_tool("analyze_reports", {
            "selected_files": saved,
            "output_format": output_format,
            "extra_description": extra_description,
            "student_name": student_name,
            "student_id": student_id,
        })

        if not wait:
            return started
        return self.wait_for_report(timeout=timeout)

    def get_pipeline_state(self) -> dict:
        """Current pipeline state (extracting → unifying → rendering → completed)."""
        return self._call_tool("get_state", {})

    def wait_for_report(self, timeout: int = 300, poll: float = 2.0) -> dict:
        """Poll the pipeline until it completes (or fails)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.get_pipeline_state()
            stage = (state.get("stage") or state.get("status") or "").lower()
            if stage in ("completed", "done", "success", "finished"):
                return state
            if stage in ("error", "failed"):
                raise RuntimeError(f"Analysis failed: {state.get('message', state)}")
            time.sleep(poll)
        raise TimeoutError(f"Analysis did not finish within {timeout}s")

    # ------------------------------------------------------------------
    # Per-student RAG (textbook learning assistant)
    # ------------------------------------------------------------------

    def ingest_textbook(
        self,
        file_path: str,
        textbook_name: str,
        student_id: str = "",
    ) -> dict:
        """Index a textbook PDF into Pinecone.

        Pass `student_id` to scope the material to that student only —
        their RAG queries will then only search their own textbook(s).
        """
        b64 = file_to_base64(file_path)
        return self._call_tool("ingest_textbook", {
            "file_bytes_b64": b64,
            "textbook_name": textbook_name,
            "file_name": Path(file_path).name,
            "student_id": student_id,
        })

    def query_textbook(
        self,
        textbook_name: str,
        question: str,
        top_k: int = 5,
        student_id: str = "",
    ) -> dict:
        """RAG query scoped to the given textbook (and student namespace)."""
        return self._call_tool("query_textbook", {
            "textbook_name": textbook_name,
            "question": question,
            "top_k": str(top_k),
            "username": student_id,
        })

    def list_textbooks(self) -> dict:
        return self._call_tool("list_textbooks", {})

    def delete_textbook(self, textbook_name: str) -> dict:
        return self._call_tool("delete_textbook", {"textbook_name": textbook_name})

    def rag_health(self) -> dict:
        return self._call_tool("health_check", {})
