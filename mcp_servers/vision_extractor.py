import json
import os
import sys
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
import fitz  # pymupdf
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Load local configuration for desktop runs and direct imports.
load_dotenv()

# Lazy client initialization to prevent import-time exceptions
client = None

# -- mcp guard -----------------------------------------------------------
# FastMCP is only required when this module is run directly as an MCP
# server (via an MCP runner or `python -m mcp_servers.vision_extractor`).
# The orchestrator imports this file for its Python functions; the mcp
# package may not be installed in that environment (e.g. Pydroid), so
# we keep the top-level import lazy and fall back to a no-op shim.
import sys as _sys
_FastMCP = None
if (
    "mcp.server.fastmcp" in _sys.modules
    or __name__ == "__main__"
    or _sys.argv[0].endswith("vision_extractor.py")
):
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]
        _FastMCP = FastMCP("vision-extractor")
    except ImportError:
        pass

class _NullServer:  # noqa
    @staticmethod
    def tool(*a, **kw):
        def deco(fn): return fn
        return deco

mcp = _FastMCP or _NullServer()
# -- end mcp guard -------------------------------------------------------


def get_client():
    global client
    if client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Gemini API key not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY in the environment or .env."
            )
        client = genai.Client(api_key=api_key)
    return client

class VisionExtractionResult(BaseModel):
    class NumericField(BaseModel):
        name: str = Field(description="Machine-friendly field name such as mathematics or total_mark")
        value: float = Field(description="Numeric value for the field")

    class StudentEntry(BaseModel):
        student_name: str = Field(description="The full name of the student")
        student_id: str = Field(description="The ID or roll number of the student")

    exam_name: str = Field(description="The generic type or name of the exam/assignment without any numbers, series, or sequence identifiers (e.g. 'Unit Test' instead of 'Unit Test 1' or 'Unit Test 2'). This acts as the assignment_name.")
    test_name: str = Field(default="", description="The specific name or number of the test indicating which test it is using Sequential Test Numbering (e.g. 'WTM 29', 'WTM 30', 'Test 1', 'Test 2', etc.).")
    data_mode: str = Field(description="Mode of the report: 'single' (only this student) or 'grouped' (class-wide report)")
    found_student: bool = Field(description="Whether the student's record/score was successfully found in this report")
    student_name: str = Field(default="", description="The name of the student found in the report.")
    student_id: str = Field(default="", description="The ID of the student found in the report.")
    student_class: str = Field(default="", description="The class of the student if found (e.g., '12').")
    student_section: str = Field(default="", description="The section or stream of the student if found (e.g., 'Science', 'A').")
    numerical_fields: list[NumericField] = Field(
        default_factory=list,
        description="All numerical fields (e.g. scores, ranks, marks) extracted for the student. Exclude non-numeric fields."
    )
    class_averages: list[NumericField] = Field(
        default_factory=list,
        description="Optional class averages or mean scores for each field if available in the report."
    )
    all_students: list[StudentEntry] = Field(
        default_factory=list,
        description="A list of all students (names and roll numbers/student IDs) listed in the report. If this is a grouped class report, list all students. If it is a single student report, list only that student."
    )


CANONICAL_SUBJECT_MAP = {
    # Physics
    "physics": "physics", "phys": "physics", "phy": "physics", "physics_marks": "physics", "physics_total": "physics",
    # Chemistry
    "chemistry": "chemistry", "chem": "chemistry", "che": "chemistry", "chemistry_marks": "chemistry", "chemistry_total": "chemistry",
    # Mathematics
    "maths": "maths", "math": "maths", "mathematics": "maths", "mat": "maths", "maths_marks": "maths", "maths_total": "maths",
    # English
    "english": "english", "eng": "english", "english_marks": "english",
    # Biology
    "biology": "biology", "bio": "biology", "biology_marks": "biology",
    # Computer Science
    "computer_science": "computer_science", "cs": "computer_science", "computer": "computer_science", "ip": "computer_science",
}

def _normalize_key(key: str) -> str:
    k = key.strip().lower()
    import re
    k = re.sub(r'[\s\-/\\]+', '_', k)
    k = re.sub(r'[^a-z0-9_]', '', k)
    k = re.sub(r'_+', '_', k)
    k = k.strip('_')
    
    if k in CANONICAL_SUBJECT_MAP:
        return CANONICAL_SUBJECT_MAP[k]
    return k

def _normalize_numeric_fields(items: list[VisionExtractionResult.NumericField] | list[dict]) -> Dict[str, float]:
    normalized: Dict[str, float] = {}
    for item in items:
        if isinstance(item, dict):
            name = item.get("name")
            value = item.get("value")
        else:
            name = item.name
            value = item.value
        if not name:
            continue
        try:
            norm_key = _normalize_key(str(name))
            normalized[norm_key] = float(value)
        except (TypeError, ValueError):
            continue
    return normalized

def build_content_parts(path: str) -> list[types.Part]:
    p = Path(path)
    ext = p.suffix.lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".pdf": "application/pdf"}
    if ext == ".pdf":
        if fitz is None:
            raise ImportError(
                'pymupdf not installed. Run: pip install pymupdf  '
                '(fitz is required for multi-page PDFs).'
            )
        # Convert every PDF page to PNG for vision (multi-page reports preserved).
        doc = fitz.open(path)
        parts = []
        try:
            for page in doc:
                pix = page.get_pixmap(dpi=150)
                parts.append(types.Part.from_bytes(data=pix.tobytes("png"), mime_type="image/png"))
        finally:
            doc.close()
        return parts
    with open(path, "rb") as f:
        return [types.Part.from_bytes(data=f.read(), mime_type=media_map.get(ext, "image/jpeg"))]

@mcp.tool()
def extract_report_data(file_path: str, student_name: str,
                          student_id: str) -> dict:
    """
    Use Gemini to extract numerical data from a report file (PDF, Image, or LLMWhisperer Markdown).
    Returns exam name, data type (single/grouped), and all numerical fields.
    """
    if file_path.lower().endswith(".md"):
        with open(file_path, "r", encoding="utf-8") as f:
            text_content = f.read()
        content_parts = [text_content]
    else:
        try:
            content_parts = build_content_parts(file_path)
        except ImportError as _imp:
            return {"error": str(_imp)}
    
    all_students_instruction = ""
    if not student_name or student_name.lower() == 'the student':
        all_students_instruction = "Also, populate the all_students list with all candidate names and roll numbers found."
    else:
        all_students_instruction = "Do NOT populate the all_students list. Leave it empty to save output tokens."

    prompt = f"""
    Extract data from this academic report.
    We are searching for student: {student_name} (ID: {student_id}).
    If student_name is 'the student' or empty, please identify the primary student in the report and extract their details.
    
    Please populate the fields in the requested schema.
    
    --- Tabular Field Discovery Instructions ---
    To identify and align data fields, look for headers and columns in the document.
    Categorize columns by the following semantic types:
    - score: Numeric score, marks, or points for a subject (e.g. Maths, Physics, Chemistry)
    - aggregate: Total marks or grand total
    - rank: Position / rank in class or test series
    - identifier: Roll number or student ID
    - name: Candidate / Student name
    
    Ensure you extract:
    1. For the exam_name field (generic assignment name): Do NOT include any sequence/series numbers (e.g. use "JEE Main WTM" instead of "JEE Main WTM 30").
    2. For the test_name field (sequential test number): e.g. "WTM 30", "WTM 31".
    3. student_name, student_id, class, and section.
    4. numerical_fields: List all subject names (scores) and aggregates.
    5. class_averages: List the average / mean score for each subject if visible.
    
    If the student is not in the report list, set found_student to false.
    {all_students_instruction}
    """
    
    genai_client = get_client()
    response = genai_client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[*content_parts, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=VisionExtractionResult,
        )
    )
    
    if response.parsed:
        parsed = response.parsed.model_dump()
    else:
        parsed = json.loads(response.text)

    parsed["numerical_fields"] = _normalize_numeric_fields(parsed.get("numerical_fields", []))
    parsed["class_averages"] = _normalize_numeric_fields(parsed.get("class_averages", []))
    return parsed

@mcp.tool()
def detect_common_data_types(results: list) -> dict:
    """
    Given a list of per-file extraction results,
    find numerical fields common across ALL files.
    """
    if not results:
        return {"common_fields": []}
    field_sets = [set(r.get("numerical_fields", {}).keys()) for r in results]
    common = set.intersection(*field_sets) if field_sets else set()
    return {"common_fields": sorted(list(common))}

if __name__ == "__main__":
    mcp.run()

# ── Supabase pre-scan markdown persistence ──
def _save_pre_scan_markdown(student_name, student_id, filepath, markdown_path):
    """Upsert Whisperer markdown into Supabase student_reports."""
    import os, re
    from pathlib import Path
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    auth = os.environ.get("SUPABASE_AUTH_HEADER", "")
    if not url or not key:
        print("[WARN] SUPABASE_URL/KEY not set; skip save.")
        return
    try:
        import httpx as _hx
    except ImportError:
        try:
            import requests as _hx
        except ImportError:
            print("[WARN] no http lib; skip save.")
            return
    try:
        md_text = Path(markdown_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print("[WARN] cannot read markdown:", e)
        return
    title = re.sub(r"[_\-]+", " ", Path(filepath).stem).strip()
    headers = {
        "apikey": key,
        "Authorization": auth or f"Bearer {key}",
        "Content-Type": "application/json",
    }
    row = {
        "student_id": student_id,
        "student_name": student_name,
        "title": title,
        "source_file": Path(filepath).name,
        "pre_scan_markdown": md_text,
        "status": "pre_scanned",
    }
    upsert_url = f"{url}/rest/v1/student_reports"
    params = {"student_id": f"eq.{student_id}", "source_file": f"eq.{Path(filepath).name}"}
    r = _hx.patch(upsert_url, headers=headers, params=params, json=row, timeout=30)
    if r.status_code not in (200, 201, 204):
        ins = _hx.post(upsert_url, headers=headers, json=row, timeout=30)
        print("[OK] inserted" if ins.status_code in (200,201,204) else f"[WARN] insert {ins.status_code}")
    else:
        print(f"[OK] upserted markdown ({student_id})")
