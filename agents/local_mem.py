"""Local storage and Supabase-backed cache for student report processing."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(override=True)

from agents.supabase_client import get_supabase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MEM_DIR = Path("local_mem")
STUDENT_REPORT_OUTPUT_DIR = PROJECT_ROOT / "Output"


def _uid():
    return os.environ.get("CURRENT_USER_ID", "00000000-0000-0000-0000-000000000000")


def _save_student_report(row: dict) -> None:
    """
    Insert or update a student_reports row using a single atomic upsert.

    The DB unique constraint is on (student_id, exam_name, test_name), so we
    use that as the ``on_conflict`` target — no pre-flight SELECT needed.
    """
    sup = get_supabase()
    row = dict(row)
    if row.get("test_name") is None:
        row["test_name"] = ""
    row.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    try:
        sup.table("student_reports").upsert(
            {
                "student_id": row["student_id"],
                "exam_name": row["exam_name"],
                "test_name": row["test_name"],
                "data": row.get("data", {}),
                "user_id": row.get("user_id") or _uid(),
                "updated_at": row["updated_at"],
            },
            on_conflict="student_id,exam_name,test_name",
        ).execute()
    except Exception as e:
        print(f"Warning: Failed to save student report: {e}")


def ensure_local_mem_dir() -> Path:
    LOCAL_MEM_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_MEM_DIR


def get_whisperer_document_path(filename: str) -> str:
    """Return local Whisperer_Out path for a given PDF filename."""
    stem = Path(filename).stem
    series, test, _ = parse_report_filename(filename)
    return str(LOCAL_MEM_DIR / series / test / "whispery_out" / f"{stem}.md")


def get_whisperer_out_dir(filename: str) -> Path:
    series, test, _ = parse_report_filename(filename)
    return LOCAL_MEM_DIR / series / test / "whispery_out"


def should_convert_file(file_path: str, whisperer_path: str) -> bool:
    p = Path(whisperer_path)
    if not p.exists():
        return True
    return Path(file_path).stat().st_mtime > p.stat().st_mtime


def save_to_whisperer_database(text: str, output_path: str) -> str:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return str(out)


def load_from_whisperer_database(output_path: str) -> str | None:
    p = Path(output_path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s/-]", "", text)
    text = re.sub(r"[\s/]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown_assignment"


def get_series_and_test_from_assignment(assignment_test: str) -> tuple[str, str, str]:
    cleaned = assignment_test.lower().replace("_", " ")
    match = re.search(r"\b(wtm|wta|ut|unit test|ut)[\s_-]*(\d+)", cleaned)
    if match:
        code_raw = match.group(1)
        num = match.group(2)
        if "wtm" in code_raw:
            code = "WTM"
        elif "wta" in code_raw:
            code = "WTA"
        elif "ut" in code_raw or "unit" in code_raw:
            code = "UT"
        else:
            code = code_raw.upper()
        test_folder = f"{code} {num}"
        exam_code = f"{code}{num}"
    else:
        test_folder = "Unknown Test"
        exam_code = "Unknown"
        code = "TEST"

    if "jee" in cleaned and "main" in cleaned:
        series_name = f"Jee Mains {code}"
    elif "jee" in cleaned and ("advance" in cleaned or "adv" in cleaned):
        series_name = f"Jee Advance {code}"
    else:
        series_name = f"Jee Mains {code}"

    return series_name, test_folder, exam_code


def get_series_info(series_name: str):
    parts = series_name.split("_", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def parse_report_filename(filename: str, target_exam_name: str | None = None) -> tuple[str, str, str]:
    if not filename:
        return target_exam_name or "Other", "Unknown", "unknown"
    slug = detect_assignment_test(filename)
    series, test, code = get_series_and_test_from_assignment(slug)
    if target_exam_name:
        series = target_exam_name
    return series, test, code


def detect_assignment_test(filename: str, exam_name: str | None = None) -> str:
    if exam_name:
        normalized = re.sub(r"\s+\d+$", "", exam_name).strip()
        return slugify(normalized)

    stem = Path(filename).stem
    has_jee = re.search(r"jee[\s_-]*main", stem, re.IGNORECASE)

    wtm_match = re.search(r"wtm[\s_-]*(\d+)", stem, re.IGNORECASE)
    if has_jee and wtm_match:
        return f"jee_main_wtm_{wtm_match.group(1)}"
    if wtm_match:
        return f"wtm_{wtm_match.group(1)}"

    wta_match = re.search(r"wta[\s_-]*(\d+)", stem, re.IGNORECASE)
    if has_jee and wta_match:
        return f"jee_main_wta_{wta_match.group(1)}"
    if wta_match:
        return f"wta_{wta_match.group(1)}"

    unit_match = re.search(r"(?:unit[\s_-]*test|ut)[\s_-]*(\d+)", stem, re.IGNORECASE)
    if unit_match:
        return f"unit_test_{unit_match.group(1)}"

    finals_match = re.search(r"(?:finals|final|term)[\s_-]*(\d*)", stem, re.IGNORECASE)
    if finals_match:
        suffix = finals_match.group(1)
        return f"finals_{suffix}" if suffix else "finals"

    return slugify(stem[:64])


def file_fingerprint(file_path: str) -> dict:
    p = Path(file_path)
    return {
        "name": p.name,
        "size": p.stat().st_size,
        "mtime": p.stat().st_mtime,
    }


def _fingerprint_matches(stored_fp: dict, file_path: str) -> bool:
    fp = file_fingerprint(file_path)
    return (
        stored_fp.get("size") == fp["size"]
        and abs(stored_fp.get("mtime", 0) - fp["mtime"]) < 1.0
    )


# ---------------------------------------------------------------
# Student directory
# ---------------------------------------------------------------

def load_student_directory() -> list[dict]:
    uid = _uid()
    supabase = get_supabase()
    try:
        result = supabase.table("profiles").select(
            "full_name,student_id,student_class,student_section"
        ).eq("uid", uid).execute()
        return result.data or []
    except Exception:
        return []


def search_student_directory(student_name_or_id: str) -> list[dict]:
    query = student_name_or_id.strip().lower()
    if not query:
        return []

    matches = []
    try:
        for student in load_student_directory():
            sn = (student.get("full_name") or student.get("student_name") or "").strip().lower()
            sid = (student.get("student_id") or "").strip().lower()
            if query == sn or query == sid or query in sn or query in sid:
                matches.append({
                    "student_name": student.get("full_name", ""),
                    "student_id": student.get("student_id", ""),
                    "student_class": student.get("student_class", ""),
                    "student_section": student.get("student_section", ""),
                })
    except Exception:
        pass
    return matches


def resolve_student_record(student_name_or_id: str) -> dict | None:
    hits = search_student_directory(student_name_or_id)
    if hits:
        return hits[0]
    return None


# ---------------------------------------------------------------
# Legacy helpers
# ---------------------------------------------------------------

def _standardize_total_mark_key(key: str) -> str:
    kl = key.lower()
    if any(x in kl for x in ("total", "grand")):
        return "total_marks"
    return key


def normalize_numeric_fields(fields: dict | list) -> dict:
    if isinstance(fields, list):
        out = {}
        for item in fields:
            name = str(item.get("name") or item.get("subject", "")).lower().replace(" ", "_")
            val = item.get("value") or item.get("score") or 0.0
            if name:
                out[_standardize_total_mark_key(name)] = float(val)
        return out
    out = {}
    for k, v in fields.items():
        out[_standardize_total_mark_key(str(k))] = float(v)
    return out


def natural_sort_key(item):
    if isinstance(item, str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", item)]
    return item


# ---------------------------------------------------------------
# Prescan cache
# ---------------------------------------------------------------

def save_cached_prescan(filename: str, file_path: str, assignment_test: str, extraction: dict) -> str:
    ensure_local_mem_dir()
    series, test, _ = parse_report_filename(filename, assignment_test)
    cache_dir = LOCAL_MEM_DIR / series
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "analyze.json"
    
    payload = {
        "assignment_test": assignment_test,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }
    if cache_path.exists():
        try:
            existing = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload.update(existing)
                payload.setdefault("files", {})
        except Exception:
            pass

    payload["files"][filename] = {
        "fingerprint": file_fingerprint(file_path),
        "file_path": file_path,
        "prescan": extraction,
    }
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # Upload to Supabase Storage
    import threading
    threading.Thread(target=upload_file_to_storage, args=(str(cache_path), f"{series}/analyze.json"), daemon=True).start()

    uid = _uid()
    supabase = get_supabase()
    student_id = extraction.get("student_id", filename)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "user_id": uid,
        "student_id": student_id,
        "exam_name": assignment_test,
        "test_name": "",
        "data": {
            "filename": filename,
            "file_path": file_path,
            "assignment_test": assignment_test,
            "extraction": extraction,
            "updated_at": now,
        },
        "updated_at": now,
    }
    try:
        _save_student_report(payload)
    except Exception as e:
        print(f"Warning: save_cached_prescan Supabase failed: {e}")
    return assignment_test


def get_cached_prescan(filename: str, file_path: str, assignment_test: str) -> dict | None:
    series, test, _ = parse_report_filename(filename, assignment_test)
    cache_path = LOCAL_MEM_DIR / series / "analyze.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            files = data.get("files", {})
            record = files.get(filename)
            if record and _fingerprint_matches(record.get("fingerprint", {}), file_path):
                return record.get("prescan")
        except Exception:
            pass

    uid = _uid()
    supabase = get_supabase()
    try:
        result = (
            supabase.table("student_reports")
            .select("data")
            .eq("user_id", uid)
            .eq("exam_name", assignment_test)
            .eq("test_name", "")
            .execute()
        )
        if result.data:
            for row in result.data:
                data = row.get("data", {})
                if data.get("filename") == filename:
                    return data.get("extraction")
    except Exception:
        pass
    return None


def archive_processed_input_file(filename: str) -> str:
    source = Path("input") / filename
    series, test, _ = parse_report_filename(filename)
    archive_root = LOCAL_MEM_DIR / series / test / "source"
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / filename
    base = destination.stem
    ext = destination.suffix
    counter = 1
    while destination.exists():
        destination = archive_root / f"{base}_{counter}{ext}"
        counter += 1
    if source.exists():
        shutil.move(str(source), str(destination))
        import threading
        threading.Thread(target=upload_file_to_storage, args=(str(destination), f"{series}/{test}/source/{destination.name}"), daemon=True).start()
    return str(destination)


# ---------------------------------------------------------------
# Phase 2 — roster
# ---------------------------------------------------------------

def run_phase_2_roster(series_name: str, file_list: list | None = None) -> dict:
    input_dir = Path("input")
    all_students = []
    all_subject_fields = set()

    whisp_out_root = ensure_local_mem_dir()
    phase2_dir = whisp_out_root / "phase_2"
    phase2_dir.mkdir(parents=True, exist_ok=True)

    for fpath in file_list or list(input_dir.iterdir()):
        try:
            text = ""
            fname = Path(fpath).name
            wpath = _whisperer_out_for(fname)
            if wpath.exists():
                text = wpath.read_text(encoding="utf-8", errors="ignore")
            else:
                continue

            exam_name = ""
            sn = ""
            sid = ""
            scls = ""
            ssec = ""
            m = re.search(r"Exam\s*[-–:]\s*(.+)", text, re.IGNORECASE)
            if not m:
                m = re.search(r"Test Name\s*[-–:]\s*(.+)", text, re.IGNORECASE)
            if m:
                exam_name = m.group(1).strip()

            m2 = re.search(
                r"Name\s*[-–:]\s*(.+?)(?:\n|ID\s*[-–:])", text, re.IGNORECASE | re.DOTALL
            )
            if m2:
                sn = m2.group(1).strip()
            m3 = re.search(r"ID\s*[-–:]\s*(\w+)", text, re.IGNORECASE)
            if m3:
                sid = m3.group(1).strip()
            m4 = re.search(r"Class\s*[-–:]\s*(\w+)", text, re.IGNORECASE)
            if m4:
                scls = m4.group(1).strip()
            m5 = re.search(r"Section\s*[-–:]\s*(.+)", text, re.IGNORECASE)
            if m5:
                ssec = m5.group(1).strip()

            for line in text.splitlines():
                mm = re.match(r"\s*-\s*([A-Za-z][A-Za-z0-9\s\(\)]*?)\s*[-–:]\s*([\d.]+)\s*", line)
                if mm:
                    all_subject_fields.add(mm.group(1).strip().lower().replace(" ", "_"))

            if sn and sid:
                all_students.append({
                    "student_name": sn,
                    "student_id": sid,
                    "student_class": scls,
                    "student_section": ssec,
                })
        except Exception as e:
            print(f"Warning: failed to parse {fpath}: {e}")

    seen = set()
    unique = []
    for s in all_students:
        if s["student_id"] not in seen:
            seen.add(s["student_id"])
            unique.append(s)

    if not all_subject_fields:
        try:
            import mcp_servers.vision_extractor as ve
            for fpath in file_list or list(input_dir.iterdir()):
                fname = Path(fpath).name
                wpath = _whisperer_out_for(fname)
                if wpath.exists():
                    # Just use the first file to get schema
                    if ve is None:
                        raise ImportError("vision_extractor module not available")
                    res = ve.extract_report_data(str(wpath), "", "")
                    if res and "numerical_fields" in res:
                        for field_name in res["numerical_fields"].keys():
                            all_subject_fields.add(field_name)
                    break
        except Exception as e:
            print(f"Warning: Gemini field detection failed: {e}")

    roster_data = {
        "series": series_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_students": len(unique),
        "students": unique,
        "common_fields": sorted(list(all_subject_fields)),
    }

    uid = _uid()
    supabase = get_supabase()
    try:
        supabase.table("student_roster").upsert({
            "user_id": uid,
            "exam_name": series_name,
            "data": roster_data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="exam_name,user_id").execute()
    except Exception as e:
        print(f"Warning: Failed to save roster: {e}")

    try:
        for s in unique:
            sid = str(s.get("student_id", "")).strip()
            if not sid:
                continue
            supabase.table("profiles").upsert({
                "uid": sid,
                "full_name": s.get("student_name", ""),
                "student_id": sid,
                "student_class": s.get("student_class", ""),
                "student_section": s.get("student_section", ""),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="uid").execute()
    except Exception as e:
        # RLS on profiles() may block service_role upserts if the
        # permissive policy below has not been applied yet.
        # Run setup_supabase_policies.sql against your Supabase project
        # (SQL Editor → paste & Run) to fix it permanently.
        print(f"Warning: Failed to update profiles (RLS may need setup_supabase_policies.sql): {e}")

    return roster_data


def _whisperer_out_for(filename: str) -> Path:
    series, test, _ = parse_report_filename(filename)
    return LOCAL_MEM_DIR / series / test / "whispery_out" / f"{Path(filename).stem}.md"


# ---------------------------------------------------------------
# Phase 3 — extraction
# ---------------------------------------------------------------

def save_phase_3_extraction(
    series_name: str,
    test_folder: str,
    exam_code: str,
    student_name: str,
    student_id: str,
    extraction: dict,
    error: str | None = None,
) -> None:
    """Phase 3: store extraction in local cache and Supabase (best effort)."""
    real_student_id = extraction.get("student_id") or student_id
    real_student_name = extraction.get("student_name") or student_name
    
    prescan_data = {
        "exam_name": extraction.get("exam_name") or series_name.upper(),
        "test_name": extraction.get("test_name") or test_folder,
        "data_mode": extraction.get("data_mode") or "grouped",
        "found_student": extraction.get("found_student", False),
        "student_name": real_student_name,
        "student_id": real_student_id,
        "student_class": extraction.get("student_class", ""),
        "student_section": extraction.get("student_section", ""),
        "numerical_fields": extraction.get("numerical_fields", {}),
        "class_averages": extraction.get("class_averages", {}),
    }
    if error:
        prescan_data["error"] = error
    if extraction.get("parse_warnings"):
        prescan_data["parse_warnings"] = extraction["parse_warnings"]

    test_dir = LOCAL_MEM_DIR / series_name / test_folder
    test_dir.mkdir(parents=True, exist_ok=True)
    json_path = test_dir / f"{test_folder}.json"
    
    existing_list = []
    if json_path.exists():
        try:
            existing_list = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(existing_list, list):
                existing_list = [existing_list] if existing_list else []
        except Exception:
            existing_list = []
            
    # Remove existing entry for this student if present, then append
    existing_list = [e for e in existing_list if str(e.get("prescan", {}).get("student_id", "")).strip() != str(real_student_id).strip() and str(e.get("prescan", {}).get("student_name", "")).strip().lower() != str(real_student_name).strip().lower()]
    existing_list.append({"prescan": prescan_data})
    
    json_path.write_text(
        json.dumps(existing_list, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    uid = _uid()
    sup = get_supabase()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        _save_student_report({
            "user_id": uid,
            "student_id": real_student_id,
            "exam_name": series_name,
            "test_name": extraction.get("test_name") or test_folder,
            "data": prescan_data,
            "updated_at": now,
        })
    except Exception as e:
        print(f"Warning: Failed to save phase-3 extraction: {e}")


def get_cached_analysis_phase_3(
    series_name: str, test_folder: str, exam_code: str, student_id: str
) -> dict | None:
    """Get cached Phase 3 results from local cache first, then Supabase."""
    json_path = LOCAL_MEM_DIR / series_name / test_folder / f"{test_folder}.json"
    if json_path.exists():
        try:
            data_list = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(data_list, list):
                data_list = [data_list]
            for item in data_list:
                prescan = item.get("prescan", {})
                if str(prescan.get("student_id", "")).strip() == str(student_id).strip():
                    return prescan
        except Exception:
            pass

    uid = _uid()
    sup = get_supabase()
    try:
        result = (
            sup.table("student_reports")
            .select("*")
            .eq("user_id", uid)
            .eq("exam_name", series_name)
            .limit(50)
            .execute()
        )
        if result.data:
            for row in result.data:
                data = row.get("data", {})
                if str(data.get("student_id", "")).strip() == str(student_id).strip():
                    return data
    except Exception:
        pass
    return None


# ---------------------------------------------------------------
# Phase 4
# ---------------------------------------------------------------

def run_phase_4_unified_data(series_name: str, student_id: str) -> dict:
    """Phase 4: build unified view from local phase-3 cache, with Supabase fallback."""
    series_dir = LOCAL_MEM_DIR / series_name
    tests = []
    student_name = ""

    if series_dir.exists():
        test_dirs = [d for d in series_dir.iterdir() if d.is_dir()]
        test_dirs.sort(key=lambda d: natural_sort_key(d.name))
        for test_dir in test_dirs:
            test_folder = test_dir.name
            json_path = test_dir / f"{test_folder}.json"
            if not json_path.exists():
                continue
            try:
                data_list = json.loads(json_path.read_text(encoding="utf-8"))
                if not isinstance(data_list, list):
                    data_list = [data_list]
                
                prescan = {}
                for item in data_list:
                    p = item.get("prescan", {})
                    if str(p.get("student_id", "")).strip() == str(student_id).strip():
                        prescan = p
                        break
                        
                found = prescan.get("found_student", False)
                scores = prescan.get("numerical_fields", {}) if found else {}
                class_avgs = prescan.get("class_averages", {}) if found else {}
                if found and not student_name:
                    student_name = prescan.get("student_name", "")
                tests.append(
                    {
                        "test_name": prescan.get("test_name", test_folder),
                        "exam_code": exam_code,
                        "found": found,
                        "scores": scores,
                        "class_averages": class_avgs,
                    }
                )
            except Exception as exc:
                print(f"Warning: Failed to load {json_path.name}: {exc}")

    if tests:
        return {
            "student_name": student_name,
            "student_id": student_id,
            "series": series_name,
            "tests": tests,
        }

    uid = _uid()
    sup = get_supabase()

    try:
        result = (
            sup.table("student_reports")
            .select("*")
            .eq("user_id", uid)
            .eq("student_id", student_id)
            .execute()
        )
    except Exception:
        result = type("R", (), {"data": []})()

    rows = result.data or []
    for row in rows:
        data = row.get("data", {})
        found = data.get("found_student", False)
        scores = data.get("numerical_fields", {}) if found else {}
        class_avgs = data.get("class_averages", {}) if found else {}
        if found and not student_name:
            student_name = data.get("student_name", "")
        tests.append({
            "test_name": data.get("test_name", ""),
            "exam_code": data.get("exam_name", ""),
            "found": found,
            "scores": scores,
            "class_averages": class_avgs,
        })

    return {
        "student_name": student_name,
        "student_id": student_id,
        "series": series_name,
        "tests": tests,
    }


def map_unified_to_aggregated(unified: dict) -> dict:
    results = []
    for test in unified["tests"]:
        if test["found"]:
            results.append({
                "exam_name": unified["series"],
                "test_name": test["test_name"],
                "found_student": True,
                "student_name": unified["student_name"],
                "student_id": unified["student_id"],
                "numerical_fields": test["scores"],
                "class_averages": test["class_averages"],
            })
    return {
        "student": {
            "name": unified["student_name"],
            "id": unified["student_id"],
            "class": "",
            "section": "",
        },
        "results": results,
    }


# ---------------------------------------------------------------
# Per-student JSON + final output
# ---------------------------------------------------------------

def maintain_per_student_json(student_name: str, student_id: str, list_of_extractions_json: str) -> str:
    import json as _json

    uid = _uid()
    sup = get_supabase()
    try:
        extractions = _json.loads(list_of_extractions_json)
    except Exception:
        extractions = []

    normalized = []
    for ex in extractions:
        normalized.append({
            "exam_name": ex.get("exam_name", ""),
            "test_name": ex.get("test_name", ""),
            "found_student": ex.get("found_student", False),
            "student_name": ex.get("student_name", student_name),
            "student_id": ex.get("student_id", student_id),
            "student_class": ex.get("student_class", ""),
            "student_section": ex.get("student_section", ""),
            "numerical_fields": normalize_numeric_fields(ex.get("numerical_fields", {})),
            "class_averages": normalize_numeric_fields(ex.get("class_averages", {})),
        })

    output_dir = STUDENT_REPORT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{student_id}_report.json"

    existing_results = []
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing_data = _json.load(f)
                existing_results = existing_data.get("results", [])
        except Exception:
            pass

    # 1. Fallback: Scan LOCAL_MEM_DIR for previous tests
    if LOCAL_MEM_DIR.exists():
        for json_path in LOCAL_MEM_DIR.rglob("*.json"):
            if json_path.name == "roster.json": continue
            try:
                data_list = _json.loads(json_path.read_text(encoding="utf-8"))
                if not isinstance(data_list, list):
                    data_list = [data_list]
                for item in data_list:
                    prescan = item.get("prescan", {})
                    # Match by ID or by Name (fallback for older tests)
                    p_id = str(prescan.get("student_id", "")).strip()
                    p_name = str(prescan.get("student_name", "")).strip().lower()
                    if p_id == str(student_id).strip() or (p_name and p_name == str(student_name).strip().lower()):
                        found = False
                        for er in existing_results:
                            if er.get("exam_name") == prescan.get("exam_name") and er.get("test_name") == prescan.get("test_name"):
                                found = True
                                break
                        if not found:
                            existing_results.append(prescan)
            except Exception:
                pass

    if sup:
        try:
            res = sup.table("student_reports").select("*").eq("user_id", uid).eq("student_id", student_id).execute()
            for row in (res.data or []):
                if row.get("exam_name") == "all_aggregated":
                    if not existing_results and row.get("data", {}).get("results"):
                        existing_results = row.get("data", {}).get("results", [])
                    continue
                
                phase3_data = row.get("data", {})
                if phase3_data:
                    found = False
                    for er in existing_results:
                        if er.get("exam_name") == phase3_data.get("exam_name") and er.get("test_name") == phase3_data.get("test_name"):
                            found = True
                            break
                    if not found:
                        existing_results.append(phase3_data)
        except Exception as e:
            print(f"Warning: Failed to fetch previous results from Supabase: {e}")

    for new_ex in normalized:
        found = False
        for ex in existing_results:
            if ex.get("exam_name") == new_ex.get("exam_name") and ex.get("test_name") == new_ex.get("test_name"):
                if str(new_ex.get("test_name", "")).lower() in ("unknown test", "unknown"):
                    continue
                ex.update(new_ex)
                found = True
                break
        if not found:
            existing_results.append(new_ex)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        _save_student_report({
            "user_id": uid,
            "student_id": student_id,
            "exam_name": "all_aggregated",
            "test_name": "",
            "data": {
                "student_name": student_name,
                "student_id": student_id,
                "results": existing_results,
                "updated_at": now,
            },
            "updated_at": now,
        })
    except Exception as e:
        print(f"Warning: maintain_per_student_json Supabase failed: {e}")

    try:
        out_path.write_text(_json.dumps({"results": existing_results}, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Warning: local write failed: {e}")
    return str(out_path)


def format_assignment_display_name(series_name: str) -> str:
    label = series_name.replace("_", " ")
    label = re.sub(r"\bwtm\b", "WTM", label, flags=re.IGNORECASE)
    label = re.sub(r"\bjee main\b", "JEE Main", label, flags=re.IGNORECASE)
    label = re.sub(r"\bunit test\b", "Unit Test", label, flags=re.IGNORECASE)
    return label


def build_scan_payload(scan_results: list) -> dict:
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
            detected_exam_name = re.sub(r"\s+\d+$", "", res.get("exam_name", "")).strip()
        if res.get("data_mode"):
            detected_data_mode = res.get("data_mode")

    student_lists_per_file = [res.get("all_students", []) for res in scan_results]
    common_students = []
    if student_lists_per_file:
        first_file_students = student_lists_per_file[0]
        other_files_students = student_lists_per_file[1:]
        for student in first_file_students:
            name = student.get("student_name", "").strip()
            student_id = str(student.get("student_id", "")).strip()
            if not name:
                continue
            norm_name = name.lower()
            is_common = True
            for other_list in other_files_students:
                found_in_other = False
                for other_student in other_list:
                    other_name = other_student.get("student_name", "").strip().lower()
                    other_id = str(other_student.get("student_id", "")).strip().lower()
                    if other_name == norm_name or (student_id and other_id == student_id.lower()):
                        found_in_other = True
                        break
                if not found_in_other:
                    is_common = False
                    break
            if is_common and not any(cs["name"].lower() == norm_name for cs in common_students):
                common_students.append({"name": name, "id": student_id})

    field_sets = [
        set(normalize_numeric_fields(res.get("numerical_fields", {})).keys())
        for res in scan_results
        if isinstance(res, dict)
    ]
    common_fields = sorted(set.intersection(*field_sets)) if field_sets else []

    return {
        "student": detected_student,
        "exam_name": detected_exam_name,
        "data_mode": detected_data_mode,
        "common_fields": common_fields,
        "common_students": sorted(common_students, key=lambda s: s["name"]),
    }


def has_history() -> bool:
    ensure_local_mem_dir()
    for item in LOCAL_MEM_DIR.iterdir():
        if item.name not in (".", "..", ".gitkeep", ".keep"):
            return True

    uid = _uid()
    sup = get_supabase()
    try:
        result = (
            sup.table("student_reports")
            .select("id", count="exact")
            .eq("user_id", uid)
            .limit(1)
            .execute()
        )
        return (result.count or 0) > 0
    except Exception:
        return False


def list_assignment_history() -> list[dict]:
    ensure_local_mem_dir()
    local_history = []
    for item in LOCAL_MEM_DIR.iterdir():
        if not item.is_file() or item.suffix.lower() != ".json":
            continue
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except Exception:
            continue
        assignment_test = data.get("assignment_test") or item.stem
        files_map = data.get("files", {})
        if not isinstance(files_map, dict):
            files_map = {}
        local_history.append(
            {
                "assignment_test": assignment_test,
                "display_name": format_assignment_display_name(assignment_test),
                "updated_at": data.get("updated_at"),
                "files": sorted(files_map.keys()),
            }
        )

    if local_history:
        local_history.sort(key=lambda h: h.get("updated_at") or "", reverse=True)
        return local_history

    uid = _uid()
    sup = get_supabase()
    try:
        result = (
            sup.table("student_reports")
            .select("exam_name,updated_at,data")
            .eq("user_id", uid)
            .order("updated_at", desc=True)
            .execute()
        )
    except Exception:
        return []

    history = []
    seen = set()
    for row in (result.data or []):
        exam = row.get("exam_name", "")
        if exam in seen:
            continue
        seen.add(exam)
        data = row.get("data", {})
        history.append({
            "assignment_test": exam,
            "display_name": format_assignment_display_name(exam),
            "updated_at": row.get("updated_at"),
            "files": ([data.get("filename", "")] if data.get("filename") else []),
        })
    return history


def save_pipeline_run(assignment_test: str, status: str, generated_files: list = None, error: str = "") -> None:
    uid = _uid()
    supabase = get_supabase()
    try:
        supabase.table("pipeline_runs").insert({
            "user_id": uid,
            "exam_name": assignment_test,
            "student_id": "",
            "status": status,
            "generated_files": generated_files or [],
            "error": error,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }).execute()
    except Exception as e:
        print(f"Warning: save_pipeline_run failed: {e}")


def get_recent_runs(limit: int = 20) -> list:
    uid = _uid()
    sup = get_supabase()
    try:
        result = (
            sup.table("pipeline_runs")
            .select("*")
            .eq("user_id", uid)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        return []


def render_final_output(student_id: str, provided_json_path: str | None = None) -> str:
    if provided_json_path and Path(provided_json_path).exists():
        agg_path = Path(provided_json_path)
    else:
        agg_path = STUDENT_REPORT_OUTPUT_DIR / f"{student_id}_report.json"

    if not agg_path.exists():
        return f"Error: Aggregated report JSON not found at {agg_path}."

    try:
        import json as _json
        data = _json.loads(agg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"Error reading report JSON: {e}"

    out_path = STUDENT_REPORT_OUTPUT_DIR / f"{student_id}_report.md"
    lines = [f"# Student Performance Report\n", f"**Student ID:** {student_id}\n"]
    for r in data.get("results", []):
        lines.append(f"## {r.get('exam_name', r.get('test_name', ''))}")
        lines.append(f"- **Name:** {r.get('student_name', '')}")
        lines.append(f"- **Class:** {r.get('student_class', '')} | **Section:** {r.get('student_section', '')}")
        lines.append("")
        lines.append("| Subject | Score | Class Average |")
        lines.append("|---------|-------|---------------|")
        nf = r.get("numerical_fields", {})
        ca = r.get("class_averages", {})
        all_keys = sorted(set(list(nf.keys()) + list(ca.keys())))
        for subj in all_keys:
            lines.append(f"| {subj} | {nf.get(subj, '-')} | {ca.get(subj, '-')} |")
        lines.append("")

    try:
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)
    except Exception as e:
        return f"Error writing report: {e}"

def list_student_reports() -> list[dict]:
    uid = _uid()
    sup = get_supabase()
    
    all_results = []
    
    # 1. Fetch from Supabase
    try:
        sup_res = (
            sup.table("student_reports")
            .select("updated_at,data")
            .eq("user_id", uid)
            .eq("exam_name", "all_aggregated")
            .order("updated_at", desc=True)
            .execute()
        )
        for row in (sup_res.data or []):
            data = row.get("data", {})
            updated_at = row.get("updated_at", "")
            student_id = data.get("student_id", "")
            for r in data.get("results", []):
                r["_updated_at"] = updated_at
                all_results.append(r)
                
            # Ensure it exists locally (sync from Supabase to local)
            if student_id:
                output_dir = STUDENT_REPORT_OUTPUT_DIR
                output_dir.mkdir(parents=True, exist_ok=True)
                out_path = output_dir / f"{student_id}_report.json"
                if not out_path.exists():
                    try:
                        import json as _json
                        out_path.write_text(_json.dumps({"results": data.get("results", [])}, indent=2), encoding="utf-8")
                    except Exception:
                        pass
    except Exception as e:
        print(f"Warning: Supabase fetch failed in list_student_reports: {e}")

    # 2. Fetch from local filesystem
    output_dir = STUDENT_REPORT_OUTPUT_DIR
    if output_dir.exists():
        import json as _json
        from datetime import datetime, timezone
        for file_path in output_dir.glob("*_report.json"):
            try:
                mtime = file_path.stat().st_mtime
                dt = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                data = _json.loads(file_path.read_text(encoding="utf-8"))
                for r in data.get("results", []):
                    r["_updated_at"] = dt
                    all_results.append(r)
            except Exception:
                pass

    # 3. Dedup by (student_id, exam_name)
    unique_results = {}
    for r in all_results:
        sid = r.get("student_id", "")
        ex = r.get("exam_name") or r.get("test_name") or "Unknown"
        key = (sid, ex)
        if key not in unique_results:
            unique_results[key] = r
        else:
            if r.get("_updated_at", "") > unique_results[key].get("_updated_at", ""):
                unique_results[key] = r

    # 4. Group by exam
    exams = {}
    for r in unique_results.values():
        ex = r.get("exam_name") or r.get("test_name") or "Unknown"
        if ex not in exams:
            exams[ex] = {
                "display_name": ex,
                "exam_names": [ex],
                "students": [],
                "files": [],
                "updated_at": r.get("_updated_at", ""),
                "student_ids_seen": set(),
                "files_seen": set()
            }
        
        if r.get("_updated_at", "") > exams[ex]["updated_at"]:
            exams[ex]["updated_at"] = r.get("_updated_at", "")
            
        sid = r.get("student_id", "")
        sname = r.get("student_name", "Unknown")
        if sid not in exams[ex]["student_ids_seen"]:
            exams[ex]["student_ids_seen"].add(sid)
            exams[ex]["students"].append({"name": sname, "id": sid})
            
        fname = r.get("source_file") or r.get("test_name") or "Report Data"
        if fname not in exams[ex]["files_seen"]:
            exams[ex]["files_seen"].add(fname)
            exams[ex]["files"].append(fname)

    # 5. Format to list
    reports = []
    for ex, data in exams.items():
        processed_files = []
        for s in data["students"]:
            sid = s["id"]
            if sid:
                # Just grab all files in the Output folder since they aren't strictly prefixed
                if STUDENT_REPORT_OUTPUT_DIR.exists():
                    for f in STUDENT_REPORT_OUTPUT_DIR.iterdir():
                        if f.is_file():
                            processed_files.append(str(f.resolve()))
        
        # Deduplicate processed files
        processed_files = list(set(processed_files))
        
        source_docs = []
        exam_dir = LOCAL_MEM_DIR / ex
        if exam_dir.exists():
            for p in exam_dir.rglob("Source.*"):
                source_docs.append({"path": str(p.resolve()), "exam": ex})
                
        reports.append({
            "display_name": data["display_name"],
            "exam_names": data["exam_names"],
            "students": data["students"],
            "files": data["files"],
            "file_count": len(data["files"]),
            "updated_at": data["updated_at"],
            "processed_files": processed_files,
            "source_docs": source_docs
        })
        
    reports.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return reports

def sync_user_data(uid: str) -> None:
    """
    Sync all user data from Supabase to local memory according to supabase_workflow.md.
    Fetches student_reports (Phase 3 & Output) and student_roster (Phase 2).
    """
    sup = get_supabase()
    if not sup: return

    # 1. Sync student_reports (Phase 3 extractions & Output reports)
    try:
        res = sup.table("student_reports").select("*").eq("user_id", uid).execute()
        for row in (res.data or []):
            exam_name = row.get("exam_name", "")
            test_name = row.get("test_name", "")
            student_id = row.get("student_id", "")
            data = row.get("data", {})
            
            if exam_name == "all_aggregated":
                if student_id:
                    out_path = STUDENT_REPORT_OUTPUT_DIR / f"{student_id}_report.json"
                    if not out_path.exists():
                        STUDENT_REPORT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                        import json as _json
                        out_path.write_text(_json.dumps({"results": data.get("results", [])}, indent=2), encoding="utf-8")
            else:
                if exam_name and test_name:
                    test_dir = LOCAL_MEM_DIR / exam_name / test_name
                    test_dir.mkdir(parents=True, exist_ok=True)
                    json_path = test_dir / f"{test_name}.json"
                    if not json_path.exists():
                        import json as _json
                        json_path.write_text(_json.dumps({"prescan": data}, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to sync student_reports: {e}")

    # 2. Sync student_roster
    try:
        res = sup.table("student_roster").select("*").eq("user_id", uid).execute()
        for row in (res.data or []):
            exam_name = row.get("exam_name", "")
            data = row.get("data", [])
            if exam_name:
                roster_path = LOCAL_MEM_DIR / exam_name / "roster.json"
                if not roster_path.exists():
                    roster_path.parent.mkdir(parents=True, exist_ok=True)
                    import json as _json
                    roster_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to sync student_roster: {e}")

    # 3. Sync physical files from Supabase Storage
    try:
        _download_storage_directory(sup, "user_data", f"{uid}/local_mem", LOCAL_MEM_DIR)
    except Exception as e:
        print(f"Warning: Failed to sync local_mem storage files: {e}")
        
    try:
        _download_storage_directory(sup, "user_data", f"{uid}/Output", STUDENT_REPORT_OUTPUT_DIR)
    except Exception as e:
        print(f"Warning: Failed to sync Output storage files: {e}")


def sync_local_to_cloud() -> None:
    """Syncs the entire local_mem and Output directories up to the user_data bucket."""
    uid = _uid()
    if not uid: return
    upload_directory_to_storage(LOCAL_MEM_DIR, f"{uid}/local_mem")
    upload_directory_to_storage(STUDENT_REPORT_OUTPUT_DIR, f"{uid}/Output")

def upload_directory_to_storage(local_dir: Path, remote_prefix: str) -> None:
    """Recursively uploads a local directory to Supabase Storage."""
    sup = get_supabase()
    if not sup or not local_dir.exists(): return
    for file_path in local_dir.rglob("*"):
        if file_path.is_file():
            try:
                rel_path = file_path.relative_to(local_dir).as_posix()
                remote_path = f"{remote_prefix}/{rel_path}".replace("\\", "/")
                with open(file_path, "rb") as f:
                    sup.storage.from_("user_data").upload(
                        file=f,
                        path=remote_path,
                        file_options={"upsert": "true"}
                    )
            except Exception as e:
                print(f"Warning: Failed to upload {file_path.name} to storage: {e}")

def upload_file_to_storage(local_path: Path | str, relative_path: str) -> None:
    """Uploads a local file to the user_data bucket in Supabase under {uid}/{relative_path}."""
    uid = _uid()
    sup = get_supabase()
    if not sup: return
    try:
        remote_path = f"{uid}/{relative_path}".replace("\\", "/")
        with open(local_path, "rb") as f:
            sup.storage.from_("user_data").upload(
                file=f,
                path=remote_path,
                file_options={"upsert": "true"}
            )
    except Exception as e:
        print(f"Warning: Failed to upload {Path(local_path).name} to Supabase storage: {e}")


def _download_storage_directory(sup, bucket: str, prefix: str, local_root: Path, base_prefix: str = None):
    """Recursively downloads all files from a Supabase bucket prefix to a local directory."""
    if base_prefix is None:
        base_prefix = prefix
        
    try:
        items = sup.storage.from_(bucket).list(prefix)
        if not items: return
        for item in items:
            name = item.get("name")
            if not name or name == ".emptyFolderPlaceholder":
                continue
            
            # In Supabase Python client, folders typically lack an id or have metadata=None
            if not item.get("id"):
                # It's a folder, recurse
                _download_storage_directory(sup, bucket, f"{prefix}/{name}", local_root, base_prefix)
            else:
                # It's a file
                file_path = f"{prefix}/{name}"
                
                # Calculate relative path to mirror locally using the original base_prefix
                if file_path.startswith(f"{base_prefix}/"):
                    rel = file_path[len(base_prefix)+1:]
                else:
                    rel = file_path
                
                dest_path = local_root / rel
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                if not dest_path.exists():
                    try:
                        res = sup.storage.from_(bucket).download(file_path)
                        with open(dest_path, "wb") as f:
                            f.write(res)
                    except Exception as e:
                        print(f"Failed to download {file_path}: {e}")
    except Exception as e:
        pass
