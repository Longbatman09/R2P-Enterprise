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
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

load_dotenv()

# Configure environment for Google AI Studio API instead of Vertex AI
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"
api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if api_key:
    os.environ["GOOGLE_API_KEY"] = api_key

# Ensure project root is in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.append(project_root)

import agents.llmwhisperer_converter as dc
import mcp_servers.vision_extractor as ve
import agents.local_mem as lm


def list_input_pdfs() -> str:
    """Lists all PDF mark sheet files in the 'input/' folder.

    Returns:
        A JSON list of PDF filenames found in the input folder.
    """
    input_dir = Path(project_root) / "input"
    if not input_dir.exists():
        return "[]"
    pdfs = [f.name for f in input_dir.iterdir() if f.suffix.lower() == ".pdf" and f.is_file()]
    return json.dumps(pdfs)


def convert_pdf_to_md(pdf_name: str) -> str:
    """Converts a PDF file in the 'input/' folder to a markdown file.

    Args:
        pdf_name: The filename of the PDF in the 'input/' folder (e.g. 'MS.1.pdf').

    Returns:
        The markdown text content of the converted file, or an error message.
    """
    try:
        output_path_str = lm.get_whisperer_document_path(pdf_name)
        output_path = Path(output_path_str)
        if output_path.exists():
            return output_path.read_text(encoding="utf-8")
        return f"Error: Converted markdown file '{output_path.name}' was not created."
    except Exception as e:
        return f"Error during PDF conversion: {e}"


def load_student_directory() -> str:
    """Reads and returns the student directory lookup table from All_stud_details.json.

    Returns:
        JSON string of the student directory lookup table.
    """
    try:
        return json.dumps(lm.load_student_directory())
    except Exception as e:
        return f"Error reading student directory: {e}"


def lookup_student(student_name_or_id: str) -> str:
    """Looks up a student in All_stud_details.json to confirm their identity.

    Args:
        student_name_or_id: The student's name or ID to look up.

    Returns:
        A JSON string of the matched student's details, or an error message.
    """
    try:
        matches = lm.search_student_directory(student_name_or_id)
        if not matches:
            return f"Error: Student '{student_name_or_id}' not found in student directory."
        elif len(matches) > 1:
            return json.dumps({"warning": "Multiple students found", "matches": matches})
        return json.dumps(matches[0])
    except Exception as e:
        return f"Error parsing student directory: {e}"


def extract_student_data_from_md(pdf_filename: str, student_name: str, student_id: str) -> str:
    """Extracts a student's marks and averages from a converted file.

    Args:
        pdf_filename: The original filename of the PDF in 'input/' (e.g. 'MS.1.pdf').
        student_name: The target student name.
        student_id: The target student ID.

    Returns:
        A JSON string of the extracted student data matching the required schema.
    """
    try:
        md_path = lm.get_whisperer_document_path(pdf_filename)
        if not Path(md_path).exists():
            return f"Error: Markdown file for '{pdf_filename}' not found."
        result = ve.extract_report_data(md_path, student_name, student_id)
        return json.dumps(result)
    except Exception as e:
        return f"Error extracting student data: {e}"


def maintain_per_student_json(student_name: str, student_id: str, list_of_extractions_json: str) -> str:
    """Aggregates test results, standardizes keys (e.g. total_marks), and maintains a per-student JSON file.

    Args:
        student_name: Full name of the student.
        student_id: ID of the student.
        list_of_extractions_json: A JSON list of all extraction results for the student from different tests.

    Returns:
        The path of the saved JSON file, or an error message.
    """
    return lm.maintain_per_student_json(student_name, student_id, list_of_extractions_json)


def render_final_output(student_id: str) -> str:
    """Reads the per-student JSON and generates the final formatted Markdown report.

    Args:
        student_id: The ID of the student to render.

    Returns:
        The markdown text of the final report, or an error message.
    """
    res = lm.render_final_output(student_id)
    if res.startswith("Error"):
        return res
    try:
        report_content = Path(res).read_text(encoding="utf-8")
        return f"Report rendered successfully and saved to Output/{student_id}_report.md:\n\n{report_content}"
    except Exception as e:
        return f"Error reading rendered report: {e}"


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-3.1-flash-lite",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are a specialized student performance marksheet analyzer agent.
Your goal is to implement the plan.md pipeline:
1. List the available PDF files in the input folder using 'list_input_pdfs'.
2. Lookup the given student name or ID in the student directory using 'lookup_student' to get the canonical details.
3. For each PDF file, convert it to markdown using 'convert_pdf_to_md'.
4. Search each converted markdown file for the target student's results using 'extract_student_data_from_md'.
5. Aggregrate all the results using 'maintain_per_student_json'.
6. Render the final report using 'render_final_output'.

Always output the final summary of the student performance report at the end of the execution.""",
    tools=[
        list_input_pdfs,
        convert_pdf_to_md,
        load_student_directory,
        lookup_student,
        extract_student_data_from_md,
        maintain_per_student_json,
        render_final_output,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
