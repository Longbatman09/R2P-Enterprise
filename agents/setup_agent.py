import os
from pathlib import Path


AGENT_CODE = """# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import re
import sys

from dotenv import load_dotenv

# Load the project .env before constructing any Gemini clients.
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(project_root, ".env"))

# Configure environments for Google AI Studio API instead of Vertex AI
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"
api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if api_key:
    os.environ["GOOGLE_API_KEY"] = api_key

# Add parent directory (project root) to path to allow importing mcp_servers
if project_root not in sys.path:
    sys.path.append(project_root)

import mcp_servers.file_watcher as fw
import mcp_servers.vision_extractor as ve
import agents.llmwhisperer_converter as dc
import agents.local_mem as lm
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini


def get_whisperer_document_path(filename: str) -> str:
    input_path = Path("input") / filename
    assignment_test = lm.detect_assignment_test(filename)
    output_dir = Path("local_mem") / assignment_test / "Whisperer_Out"
    output_path = output_dir / f"{input_path.stem}.md"
    
    if not input_path.exists():
        return str(output_path)
        
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_path.exists() or input_path.stat().st_mtime > output_path.stat().st_mtime:
        dc.convert_file(input_path, output_dir)
        
    return str(output_path)


def scan_input_folder() -> str:
    \"\"\"Pre-scan the first input files and return a JSON payload.\"\"\"
    inputs = fw.list_input_files()
    files = inputs.get("files", [])
    if not files:
        return json.dumps({"error": "No files found in input/ folder", "common_fields": []})

    scan_results = []
    for filename in files[:2]:
        try:
            whisperer_path = get_whisperer_document_path(filename)
            res = ve.extract_report_data(whisperer_path, student_name="the student", student_id="any ID")
            scan_results.append(res)
        except Exception as ex:
            print(f"Pre-scan error: {ex}")

    detected_student = {"name": "", "id": "", "class": "", "section": ""}
    detected_exam_name = "Unit Test"
    detected_data_mode = "single"

    for res in scan_results:
        if res.get("found_student"):
            if res.get("student_name"):
                detected_student["name"] = res.get("student_name")
            if res.get("student_id"):
                detected_student["id"] = res.get("student_id")
            if res.get("student_class"):
                detected_student["class"] = res.get("student_class")
            if res.get("student_section"):
                detected_student["section"] = res.get("student_section")
        if res.get("exam_name"):
            raw_exam = res.get("exam_name")
            detected_exam_name = re.sub(r"\\s+\\d+$", "", raw_exam).strip()
        if res.get("data_mode"):
            detected_data_mode = res.get("data_mode")

    common = ve.detect_common_data_types(scan_results)

    response_payload = {
        "student": detected_student,
        "exam_name": detected_exam_name,
        "data_mode": detected_data_mode,
        "common_fields": common.get("common_fields", []),
    }
    return json.dumps(response_payload)


root_agent = Agent(
    name="scanner_agent",
    model=Gemini(model="gemini-3.1-flash-lite"),
    instruction=\"\"\"You are a specialized report scanner agent.
When asked to scan the input folder, use the 'scan_input_folder' tool and return the resulting JSON string directly without extra explanation or markdown block symbols.\"\"\",
    tools=[scan_input_folder],
)

app = App(root_agent=root_agent, name="app")
"""


SCAN_AGENT_PYPROJECT = """[project]
name = "scan-agent"
version = "0.1.0"
description = "Local report pre-scan agent for Report2Plot"
authors = [
    {name = "Cursor", email = "noreply@example.com"},
]
dependencies = [
    "google-adk[gcp]>=2.0.0,<3.0.0",
    "opentelemetry-instrumentation-google-genai>=0.1.0,<1.0.0",
    "gcsfs>=2024.11.0",
    "google-cloud-logging>=3.12.0,<4.0.0",
    "google-cloud-aiplatform[evaluation,agent-engines]>=1.156.0",
    "protobuf>=6.31.1,<7.0.0",
    "fastmcp>=2.10.5",
    "pymupdf>=1.24.0",
    "google-genai>=1.24.0",
    "python-dotenv>=1.0.1",
]
requires-python = ">=3.11,<3.14"

[dependency-groups]
dev = [
    "pytest>=9.0.2,<10.0.0",
    "pytest-asyncio>=1.0.0,<2.0.0",
    "nest-asyncio>=1.6.0,<2.0.0",
]

[project.optional-dependencies]
eval = [
    "google-adk[eval]>=2.0.0,<3.0.0",
    "google-cloud-aiplatform[evaluation]>=1.156.0",
]
lint = [
    "ruff>=0.4.6,<1.0.0",
    "ty>=0.0.1a0",
    "codespell>=2.2.0,<3.0.0",
]

[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = [
    "E",
    "F",
    "W",
    "I",
    "C",
    "B",
    "UP",
    "RUF",
]
ignore = ["E501", "C901", "B006"]

[tool.ruff.lint.isort]
known-first-party = ["app", "frontend"]

[tool.ty.environment]
python-version = "3.10"

[tool.ty.src]
exclude = [".venv/**"]

[tool.ty.rules]
unresolved-import = "ignore"
unresolved-attribute = "ignore"
invalid-argument-type = "ignore"
invalid-assignment = "ignore"
invalid-return-type = "ignore"
possibly-missing-attribute = "ignore"
not-subscriptable = "ignore"
deprecated = "ignore"

[tool.codespell]
ignore-words-list = "rouge"
skip = "./locust_env/*,uv.lock,.venv,./frontend,**/package-lock.json"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
pythonpath = "."
asyncio_default_fixture_loop_scope = "function"

[tool.hatch.build.targets.wheel]
packages = ["app", "frontend"]
"""


def write_if_changed(path: Path, content: str) -> bool:
    normalized = content.strip() + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == normalized:
        return False
    path.write_text(normalized, encoding="utf-8")
    return True


def setup() -> None:
    scan_agent_dir = Path("scan-agent")
    app_dir = scan_agent_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    agent_changed = write_if_changed(app_dir / "agent.py", AGENT_CODE)
    pyproject_changed = write_if_changed(scan_agent_dir / "pyproject.toml", SCAN_AGENT_PYPROJECT)

    print(
        "scan-agent setup complete:"
        f" agent.py {'updated' if agent_changed else 'unchanged'},"
        f" pyproject.toml {'updated' if pyproject_changed else 'unchanged'}."
    )


if __name__ == "__main__":
    setup()
