"""R2P School SDK — one-liner integration for schools.

pip install r2p-school-sdk  (in your own pyproject.toml, path: ../r2p_school_sdk)

Usage:
    from r2p_school_sdk import R2PSchoolClient

    client = R2PSchoolClient(
        api_url="https://<your-render-app>.onrender.com",
        api_key="<student-or-school-api-key>",
    )

    # Upload a report PDF
    result = client.upload_report(
        file_path="report.pdf",
        student_name="Aarav Sharma",
        student_id="10-A",
        output_format="pptx",
    )
    print(result)
"""

from __future__ import annotations

import base64
from typing import Any

import requests

from .utils import file_to_base64


class R2PSchoolClient:
    """Thin wrapper around the R2P-Enterprise MCP backend."""

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
    # School-facing helpers
    # ------------------------------------------------------------------

    def upload_report(
        self,
        file_path: str,
        student_name: str,
        student_id: str,
        output_format: str = "pptx",
        extra_description: str = "",
    ) -> dict:
        """Upload a PDF or image report and run the analysis pipeline."""
        b64 = file_to_base64(file_path)
        return self._call_tool("analyze_reports", {
            "selected_files": b64,
            "output_format": output_format,
            "extra_description": extra_description,
            "student_name": student_name,
            "student_id": student_id,
        })

    def query_textbook(
        self,
        textbook_name: str,
        question: str,
        top_k: int = 5,
        student_id: str = "",
    ) -> dict:
        """RAG query scoped to the given textbook."""
        return self._call_tool("query_textbook", {
            "textbook_name": textbook_name,
            "question": question,
            "top_k": str(top_k),
            "username": student_id,
        })

    def list_textbooks(self) -> dict:
        return self._call_tool("list_textbooks", {})

    def ingest_textbook(
        self,
        file_path: str,
        textbook_name: str,
    ) -> dict:
        b64 = file_to_base64(file_path)
        return self._call_tool("ingest_textbook", {
            "file_bytes_b64": b64,
            "textbook_name": textbook_name,
            "file_name": file_path.split("/")[-1],
        })
