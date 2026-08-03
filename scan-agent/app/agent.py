# Copyright 2026 Google LLC
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
    """Pre-scan the first input files and return a JSON payload."""
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
            detected_exam_name = re.sub(r"\s+\d+$", "", raw_exam).strip()
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
    instruction="""You are a specialized report scanner agent.
When asked to scan the input folder, use the 'scan_input_folder' tool and return the resulting JSON string directly without extra explanation or markdown block symbols.""",
    tools=[scan_input_folder],
)

app = App(root_agent=root_agent, name="app")
