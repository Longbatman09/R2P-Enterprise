import base64
import os
from pathlib import Path
import agents.orchestrator as orch

def upload_files(files: list[dict]) -> dict:
    """
    Upload files as base64 to the input folder.
    """
    try:
        saved_files = []
        input_dir = Path(orch.project_root) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            name = f.get("name")
            data = f.get("data")
            if not name or not data:
                continue
            file_path = input_dir / name
            with open(file_path, "wb") as fh:
                fh.write(base64.b64decode(data))
            saved_files.append(name)
        return {"files": saved_files}
    except Exception as e:
        return {"error": str(e)}

def prescan_selected(selected_files: list[str]) -> dict:
    """
    Prescan selected files.
    """
    try:
        return orch.prescan_selected_files(selected_files)
    except Exception as e:
        return {"error": str(e)}

def analyze_reports(selected_files: list[str], output_format: str, extra_description: str, student_name: str, student_id: str) -> dict:
    """
    Run the analysis pipeline.
    """
    try:
        # Start a background thread via orchestrator logic
        import threading
        
        def run_thread():
            try:
                orch.set_state("extracting", 0, len(selected_files), "Extracting test data...")
                assignment_test = "Unknown"
                for idx, fname in enumerate(selected_files):
                    orch.set_state("extracting", idx + 1, len(selected_files), f"Extracting {fname}...", current_file=fname)
                    fpath = os.path.join(orch.project_root, "input", fname)
                    res, cur_assignment_test, _ = orch.extract_for_analysis(fpath, student_name, student_id)
                    assignment_test = cur_assignment_test
                    orch.lm.save_phase_3_extraction(assignment_test, Path(fname).stem, "UNKNOWN", student_name, student_id, res)
                
                orch.set_state("unifying", 1, 1, "Creating unified report...")
                unified = orch.lm.run_phase_4_unified_data(assignment_test, student_id)
                agg = orch.lm.map_unified_to_aggregated(unified)
                
                import json
                json_path = orch.lm.maintain_per_student_json(student_name, student_id, json.dumps(agg.get("results", [])))
                
                orch.set_state("rendering", 1, 1, "Generating plots...")
                output_str = orch.lm.render_final_output(student_id, json_path)
                
                if output_format in ["both", "charts"]:
                    import mcp_servers.plot_renderer as pr
                    if pr is not None:
                        try:
                            with open(json_path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            if 'results' in data and data['results']:
                                for t in data['results']:
                                    test = t.get('test_name', 'Test')
                                    exam = t.get('exam_name', 'Exam')
                                    title = f"{exam} - {test}"
                                    num_fields = t.get("numerical_fields", {})
                                    if num_fields:
                                        pr.render_chart("bar", title, list(num_fields.keys()), list(num_fields.values()), 
                                                      "Subject", "Score", os.path.join(orch.project_root, "Output", f"{student_id}_{test}_plot.png"))
                        except Exception as e:
                            print(f"Plotting error: {e}")

                orch.lm.save_pipeline_run(assignment_test, "success", [json_path], "")
                orch.set_state("completed", 1, 1, "Analysis complete.")
            except Exception as e:
                orch.set_state("error", 0, 0, f"Error: {e}")
                
        threading.Thread(target=run_thread, daemon=True).start()
        return {"status": "started"}
    except Exception as e:
        return {"error": str(e)}

def get_state() -> dict:
    """
    Get the current pipeline state.
    """
    return orch.get_state()

# Directories inside the workspace that generated outputs may live in.
# The read_file tool refuses anything outside these (prevents reading secrets
# such as .env via a crafted path).
_ALLOWED_OUTPUT_ROOTS = (
    "input",
    "Output",
    "output",
    "local_mem",
    "rag_data",
    "Archived_Files",
    "students",
)


def read_file(path: str) -> dict:
    """
    Read file contents and return as base64 (restricted to workspace outputs).
    """
    try:
        import base64
        project_root = Path(orch.project_root).resolve()
        target = Path(path).expanduser().resolve()
        allowed = any(
            target.is_relative_to((project_root / root).resolve())
            for root in _ALLOWED_OUTPUT_ROOTS
        )
        if not allowed:
            return {
                "error": "Access denied: path must be inside the workspace output folders."
            }
        with open(target, "rb") as f:
            data = f.read()
            ext = os.path.splitext(str(target))[1].lower()
            if ext in [".html", ".md", ".json"]:
                return {"text": data.decode("utf-8", errors="replace")}
            return {"base64": base64.b64encode(data).decode("utf-8")}
    except Exception as e:
        return {"error": str(e)}
