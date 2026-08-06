from pathlib import Path
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(project_root, ".env"), override=True)

try:
    import mcp_servers.plot_renderer as pr
except Exception:
    pr = None

try:
    import mcp_servers.vision_extractor as ve
except Exception:
    ve = None

try:
    import mcp_servers.file_watcher as fw
except Exception:
    fw = None

try:
    import mcp_servers.rag_system as rag
except Exception:
    rag = None

import agents.llmwhisperer_converter as dc
import agents.local_mem as lm
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
import time
import json
from email.parser import BytesParser
from email.policy import default

import agents.local_mem as lm
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
import time
import json
from email.parser import BytesParser
from email.policy import default

try:
    from agents.firebase_auth import verify_id_token as _verify_token
    _firebase_auth_available = True
except Exception:
    _firebase_auth_available = False

pipeline_state = {
    "stage": "idle",
    "progress_current": 0,
    "progress_total": 0,
    "message": "",
    "files": [],
    "user_id": None,
}
state_lock = threading.Lock()
pipeline_lock = threading.Lock()
ignore_next_instruction_mtime = None

active_clients = {}
heartbeat_received = False
last_empty_time = None
startup_time = time.time()


def check_heartbeat_loop(httpd):
    global active_clients, heartbeat_received, last_empty_time
    while True:
        time.sleep(1.0)
        now = time.time()
        with state_lock:
            expired_clients = [
                cid for cid, last_seen in active_clients.items()
                if now - last_seen > 300.0
            ]
            for cid in expired_clients:
                del active_clients[cid]
            if active_clients:
                last_empty_time = None
            elif not heartbeat_received and last_empty_time is None:
                last_empty_time = now


def set_state(stage, current, total, message, files=None, current_file=None, current_markdown=None, current_jsonpreview=None):
    with state_lock:
        pipeline_state["stage"] = stage
        pipeline_state["progress_current"] = current
        pipeline_state["progress_total"] = total
        pipeline_state["message"] = message
        if files is not None:
            pipeline_state["files"] = list(files)
        pipeline_state["current_file"] = current_file
        pipeline_state["current_markdown"] = current_markdown
        pipeline_state["current_jsonpreview"] = current_jsonpreview


def get_state():
    with state_lock:
        return dict(pipeline_state)


def build_scan_payload(scan_results):
    return lm.build_scan_payload(scan_results)


def extract_for_prescan(filename: str):
    file_path = os.path.join(project_root, "input", filename)
    assignment_test = lm.detect_assignment_test(filename)
    cached = lm.get_cached_prescan(filename, file_path, assignment_test)
    if cached:
        print(f"[local_mem] Reusing cached pre-scan for {filename} ({assignment_test})")
        return cached, assignment_test, True

    out_dir = lm.get_whisperer_out_dir(file_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = lm.get_whisperer_document_path(file_path)
    if os.path.exists(file_path):
        if lm.should_convert_file(file_path, markdown_path):
            dc.convert_file(Path(file_path), out_dir)
    else:
        print(f"Warning: Input file missing during pre-scan: {file_path}")

    if ve is None:
        raise ImportError(
            "vision_extractor module not available. "
            "Install dependencies: pip install fastmcp pymupdf google-genai "
            "pydantic pillow"
        )
    result = ve.extract_report_data(markdown_path, student_name="the student", student_id="any ID")
    if result.get("exam_name"):
        assignment_test = lm.detect_assignment_test(filename, result["exam_name"])
    lm.save_cached_prescan(filename, file_path, assignment_test, result)
    print(f"[local_mem] Saved pre-scan for {filename} -> local_mem/{assignment_test}")
    return result, assignment_test, False


def prescan_input_files():
    """
    Prescan all input files.
    """
    inputs = fw.list_input_files()
    files = inputs.get("files", [])
    if not files:
        set_state("scanning", 0, 0, "No files found to scan.")
        return {
            "common_fields": [],
            "common_students": [],
            "student": {"name": "", "id": "", "class": "", "section": ""},
        }

    lm.ensure_local_mem_dir()
    set_state("scanning", 0, len(files), "Initializing scan of input files...")

    scan_results = []
    series_names = set()
    scan_errors = []
    for idx, filename in enumerate(files):
        abs_path = os.path.join(project_root, "input", filename)
        set_state("scanning", idx + 1, len(files), f"Converting and parsing {filename}...", current_file=abs_path)
        try:
            res, assignment_test, _ = extract_for_prescan(filename)
            scan_results.append(res)
            series_names.add(assignment_test)
        except Exception as ex:
            scan_errors.append(f"{filename}: {ex}")
            print(f"Warning: Pre-scan failed for {filename}: {ex}")

    if not scan_results:
        set_state("idle", 0, 0, "Pre-scan failed.")
        raise RuntimeError(
            "Pre-scan failed for every report. "
            + ("Details: " + "; ".join(scan_errors) if scan_errors else "")
        )

    payload = build_scan_payload(scan_results)
    payload["selected_files"] = files
    payload["series_names"] = sorted(series_names)
    if series_names:
        payload["exam_name"] = lm.format_assignment_display_name(sorted(series_names)[0])
    set_state("idle", 0, 0, "Idle")
    return payload


def prescan_selected_files(selected_files):
    files = [f for f in selected_files if f]
    if not files:
        set_state("scanning", 0, 0, "No selected files to scan.")
        return {
            "common_fields": [],
            "common_students": [],
            "student": {"name": "", "id": "", "class": "", "section": ""},
            "exam_name": "",
            "selected_files": [],
        }

    lm.ensure_local_mem_dir()
    set_state("scanning", 0, len(files), "Initializing scan of selected files...")
    scan_results = []
    series_names = set()
    scan_errors = []
    for idx, filename in enumerate(files):
        abs_path = os.path.join(project_root, "input", filename)
        set_state("scanning", idx + 1, len(files), f"Converting and parsing {filename}...", current_file=abs_path)
        try:
            res, assignment_test, _ = extract_for_prescan(filename)
            scan_results.append(res)
            series_names.add(assignment_test)
        except Exception as ex:
            scan_errors.append(f"{filename}: {ex}")
            print(f"Warning: Selected-file pre-scan failed for {filename}: {ex}")

    if not scan_results:
        set_state("idle", 0, 0, "Pre-scan failed.")
        raise RuntimeError(
            "Pre-scan failed for every selected report. "
            + ("Details: " + "; ".join(scan_errors) if scan_errors else "")
        )

    payload = build_scan_payload(scan_results)
    payload["selected_files"] = files
    payload["series_names"] = sorted(series_names)
    if series_names:
        payload["exam_name"] = lm.format_assignment_display_name(sorted(series_names)[0])
    set_state("idle", 0, 0, "Idle")
    return payload


def validate_selected_files(files):
    if not files:
        raise ValueError("No files selected for analysis.")
    allowed_ext = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext not in allowed_ext:
            raise ValueError(f"Unsupported file type: {f}")


def extract_for_analysis(filepath, student_name, student_id):
    markdown_path = lm.get_whisperer_document_path(filepath)
    if not os.path.exists(markdown_path):
        out_dir = lm.get_whisperer_out_dir(filepath)
        out_dir.mkdir(parents=True, exist_ok=True)
        dc.convert_file(Path(filepath), out_dir)

    if not os.path.exists(markdown_path):
        raise RuntimeError("LLMWhisperer conversion failed.")

    if ve is None:
        raise ImportError(
            "vision_extractor module not available. "
            "Install dependencies: pip install fastmcp pymupdf google-genai "
            "pydantic pillow"
        )
    extraction = ve.extract_report_data(markdown_path, student_name, student_id)
    if not extraction:
        raise RuntimeError("Data extraction failed.")
    filename = os.path.basename(filepath)
    series_name, _, _ = lm.parse_report_filename(filename)
    extraction.setdefault("test_name", Path(filename).stem)
    extraction.setdefault("source_file", filename)
    return extraction, series_name, markdown_path


def archive_selected_files(files):
    import shutil
    archived = []
    archive_dir = os.path.join(project_root, "Archived_Files")
    os.makedirs(archive_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    for f in files:
        base = os.path.basename(f)
        dest = os.path.join(archive_dir, f"{ts}_{base}")
        shutil.move(f, dest)
        archived.append(dest)
    return archived


def clear_input_folder():
    input_dir = os.path.join(project_root, "input")
    if os.path.isdir(input_dir):
        for f in os.listdir(input_dir):
            fp = os.path.join(input_dir, f)
            if os.path.isfile(fp):
                os.remove(fp)


def serve_static(handler, path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        ctype = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        handler.send_response(200)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except FileNotFoundError:
        handler.send_error(404, "File not found")


class OrchestratorHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def handle_one_request(self):
        """Catch ALL exceptions so a single bad request never kills the server."""
        try:
            super().handle_one_request()
        except Exception as e:
            try:
                self.send_error(500, f"Internal error: {e}")
            except Exception:
                pass  # Connection already broken — nothing we can do

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization")
        self.end_headers()

    def _send_json(self, obj, code=200):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _extract_user(self):
        """Return the authenticated user's ID from a Supabase access token or session.json."""
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            session_file = os.path.join(project_root, "session.json")
            if os.path.exists(session_file):
                try:
                    with open(session_file, "r") as f:
                        session_data = json.load(f)
                    return session_data.get("user_id")
                except Exception:
                    pass
            return None
        token = auth_header[7:]
        if not _firebase_auth_available:
            return None
        try:
            decoded = _verify_token(token)
            if decoded:
                # Firebase tokens use "uid"; Supabase tokens use "sub"
                return decoded.get("sub") or decoded.get("uid") or decoded.get("user_id")
        except Exception:
            pass
        return None

    def _get_user_context(self):
        user_id = self._extract_user()
        if user_id:
            os.environ["CURRENT_USER_ID"] = str(user_id)
        return user_id

    def _serve_page(self, filename):
        filepath = os.path.join(project_root, "ui", filename)
        if not os.path.isfile(filepath):
            self.send_error(404, f"Page '{filename}' not found")
            return
        serve_static(self, filepath)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        self._get_user_context()

        if path == "/":
            self._serve_page("login_page.html")
        elif path == "/login_page.html" or path == "/login":
            self._serve_page("login_page.html")
        elif path == "/login_bg.jpg":
            self._serve_page("login_bg.jpg")
        elif path == "/dashboard.html" or path == "/dashboard":
            self._serve_page("dashboard.html")
        elif path == "/index.html":
            self._serve_page("description_page.html")
        elif path == "/description_page.html":
            self._serve_page("description_page.html")
        elif path == "/exam_detail_page.html":
            self._serve_page("exam_detail_page.html")
        elif path == "/history" or path == "/history_page.html":
            self._serve_page("history_page.html")
        elif path == "/reports":
            self._serve_page("reports.html")
        elif path == "/students":
            self._serve_page("students.html")
        elif path == "/settings":
            self._serve_page("settings.html")
        elif path == "/api/state":
            self._send_json(get_state())
        elif path == "/api/heartbeat":
            client_id = f"client_{id(self)}"
            with state_lock:
                active_clients[client_id] = time.time()
                global heartbeat_received
                heartbeat_received = True
            self._send_json({"status": "ok", "client_id": client_id})
        elif path == "/api/auth/session":
            session_file = os.path.join(project_root, "session.json")
            if os.path.exists(session_file):
                with open(session_file, "r") as f:
                    self._send_json(json.load(f))
            else:
                self._send_json({}, code=401)
        elif path.startswith("/api/history"):
            self._handle_reports_list()
        elif path.startswith("/api/roster"):
            self._handle_roster_get()
        elif path.startswith("/api/profile"):
            self._handle_profile_get()
        elif path == "/rag-chat":
            self._serve_page("rag_chat.html")
        elif path.startswith("/api/rag/textbooks"):
            self._handle_rag_list()
        elif path == "/api/rag/health":
            self._handle_rag_health()
        elif path == "/api/file":
            self._handle_file_api()
        else:
            self.send_error(404, "Not Found")

    def _handle_file_api(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        file_path = params.get("path", [""])[0]

        if not file_path or not os.path.exists(file_path):
            self.send_error(404, "File Not Found")
            return

        abs_target = os.path.abspath(file_path)
        if not abs_target.startswith(os.path.abspath(project_root)):
            self.send_error(403, "Forbidden")
            return

        try:
            with open(abs_target, "rb") as f:
                content = f.read()
            self.send_response(200)
            
            ext = os.path.splitext(abs_target)[1].lower()
            if ext == ".pdf":
                ctype = "application/pdf"
            elif ext in [".png", ".jpg", ".jpeg"]:
                ctype = f"image/{ext[1:]}"
            elif ext == ".json":
                ctype = "application/json"
            elif ext == ".md":
                ctype = "text/markdown"
            elif ext in [".html", ".htm"]:
                ctype = "text/html"
            else:
                ctype = "application/octet-stream"

            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def do_POST(self):
        self._get_user_context()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}

        path = urllib.parse.urlparse(self.path).path

        if path == "/api/auth/login":
            self._handle_auth_login(body)
        elif path == "/api/auth/signup":
            self._handle_auth_signup(body)
        elif path == "/api/upload":
            self._handle_upload(body)
        elif path == "/api/auth/logout":
            self._handle_auth_logout()
        elif path == "/api/auth/update_account":
            self._handle_auth_update_account(body)
        elif path == "/api/auth/delete_account":
            self._handle_auth_delete_account()
        elif path == "/api/analyze" or path == "/api/run":
            self._handle_run(body)
        elif path == "/api/scan":
            self._handle_scan(body)
        elif path == "/api/stop":
            self._handle_stop()
        elif path.startswith("/api/report"):
            self._handle_report_save(body)
        elif path.startswith("/api/roster"):
            self._handle_roster_save(body)
        elif path.startswith("/api/profile"):
            self._handle_profile_save(body)
        elif path.startswith("/api/rag"):
            self._handle_rag_post(body)
        elif path == "/api/clear_memory":
            self._handle_clear_memory()
        else:
            self.send_error(404, "Not Found")

    def _handle_clear_memory(self):
        try:
            # Clear local memory
            local_mem_dir = os.path.join(project_root, "local_mem")
            if os.path.exists(local_mem_dir):
                import shutil
                shutil.rmtree(local_mem_dir)
            
            output_dir = os.path.join(project_root, "Output")
            if os.path.exists(output_dir):
                import shutil
                shutil.rmtree(output_dir)
                
            self._send_json({"status": "success"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_reports_list(self):
        try:
            reports = lm.list_student_reports()
            self._send_json({
             "has_history": len(reports) > 0,
             "items": reports,
             "count": len(reports),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_roster_get(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            exam_name = (qs.get("exam_name") or [""])[0]
            test_folder = (qs.get("test_folder") or [""])[0]
            data = lm.load_student_roster(exam_name, test_folder)
            self._send_json({"roster": data or []})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_profile_get(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            search = (qs.get("q") or [""])[0]
            data = lm.search_student_directory(search)
            self._send_json({"directory": data})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_roster_save(self, body):
        try:
            exam_name = body.get("exam_name", "")
            test_folder = body.get("test_folder", "")
            roster = body.get("roster", [])
            lm.save_student_roster(exam_name, test_folder, roster)
            self._send_json({"status": "ok"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_report_save(self, body):
        try:
            lm.save_report_json(body)
            self._send_json({"status": "ok"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_profile_save(self, body):
        try:
            uid = self._get_user_context()
            if not uid:
                self._send_json({"error": "Unauthorized"}, code=401)
                return
            lm.upsert_profile(body)
            self._send_json({"status": "ok"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_auth_login(self, data):
        email = data.get("email")
        password = data.get("password")
        if not email or not password:
            self._send_json({"error": "Email and password required"}, code=400)
            return
        
        try:
            from agents.supabase_client import get_supabase
            supabase = get_supabase(use_service_role=False)
        except Exception:
            supabase = None
        if not supabase:
            # Fallback mock for testing without DB
            session_data = {
                "user_id": "mock_id_123",
                "username": email.split('@')[0],
                "refresh_token": "mock_token",
                "expires_at": "2099-12-31T23:59:59Z",
                "device_id": "mock_device"
            }
            with open(os.path.join(project_root, "session.json"), "w") as f:
                json.dump(session_data, f)
            self._send_json(session_data)
            return

        try:
            res = supabase.auth.sign_in_with_password({"email": email, "password": password})
            session_data = {
                "user_id": res.user.id,
                "username": res.user.user_metadata.get("name", email.split('@')[0]),
                "email": res.user.email,
                "dob": res.user.user_metadata.get("dob", ""),
                "class": res.user.user_metadata.get("class", ""),
                "school": res.user.user_metadata.get("school", ""),
                "refresh_token": res.session.refresh_token,
                "expires_at": str(res.session.expires_at),
                "device_id": "device_web"
            }
            with open(os.path.join(project_root, "session.json"), "w") as f:
                json.dump(session_data, f)
            
            # Start background sync task as defined in supabase_workflow.md
            import threading
            import agents.local_mem as lm
            
            set_state("syncing", 0, 0, "Retrieving your data securely from the cloud...")
            
            def run_login_sync():
                try:
                    lm.sync_user_data(res.user.id)
                except Exception as e:
                    print(f"Login sync error: {e}")
                set_state("idle", 0, 0, "Sync complete")
                
            threading.Thread(target=run_login_sync, daemon=True).start()
            
            self._send_json(session_data)
        except Exception as e:
            err_msg = str(e)
            if "Invalid" in err_msg or "invalid" in err_msg:
                err_msg = "Invalid login credentials"
            self._send_json({"error": err_msg}, code=401)

    def _handle_auth_signup(self, data):
        email = data.get("email")
        password = data.get("password")
        name = data.get("name")
        dob = data.get("dob")
        student_class = data.get("class")
        school = data.get("school")
        
        if not all([email, password, name, dob, student_class, school]):
            self._send_json({"error": "All fields are required"}, code=400)
            return

        import re
        if not re.match(r"^(?=.*\d)(?=.*[A-Z]).{8,}$", password):
            self._send_json({"error": "Password does not meet requirements"}, code=400)
            return

        try:
            from agents.supabase_client import get_supabase
            supabase = get_supabase(use_service_role=False)
        except Exception:
            supabase = None
        if not supabase:
            # Fallback mock
            session_data = {
                "user_id": "mock_id_new",
                "username": name,
                "refresh_token": "mock_token",
                "expires_at": "2099-12-31T23:59:59Z",
                "device_id": "mock_device"
            }
            with open(os.path.join(project_root, "session.json"), "w") as f:
                json.dump(session_data, f)
            self._send_json(session_data)
            return

        try:
            # Step 1: Use the Admin API (Service Role) to create and auto-confirm the user, bypassing the email rate limiter (429 error).
            supabase_admin = get_supabase(use_service_role=True)
            new_user = supabase_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "name": name,
                    "dob": dob,
                    "class": student_class,
                    "school": school
                }
            })
            
            if not new_user or not new_user.user:
                self._send_json({"error": "Signup failed to create user"}, code=400)
                return

            # Step 2: Now sign in using the Anon key to generate the proper session tokens
            supabase_anon = get_supabase(use_service_role=False)
            res = supabase_anon.auth.sign_in_with_password({"email": email, "password": password})
            
            if res.user:
                session_data = {
                    "user_id": res.user.id,
                    "username": name,
                    "email": email,
                    "dob": dob,
                    "class": student_class,
                    "school": school,
                    "refresh_token": res.session.refresh_token if res.session else "no_token_yet",
                    "expires_at": str(res.session.expires_at) if res.session else "pending",
                    "device_id": "device_web"
                }
                with open(os.path.join(project_root, "session.json"), "w") as f:
                    json.dump(session_data, f)
                self._send_json(session_data)
            else:
                self._send_json({"error": "Signup succeeded but login failed, please log in manually"}, code=400)
        except Exception as e:
            err_msg = str(e)
            if "already registered" in err_msg.lower():
                err_msg = "User already exists. Please log in instead."
            self._send_json({"error": err_msg}, code=400)

    def _handle_auth_logout(self):
        try:
            session_file = os.path.join(project_root, "session.json")
            if os.path.exists(session_file):
                os.remove(session_file)
            self._send_json({"status": "ok"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_auth_update_account(self, data):
        try:
            uid = self._extract_user()
            if not uid:
                self._send_json({"error": "Unauthorized"}, code=401)
                return

            name = data.get("name")
            dob = data.get("dob")
            student_class = data.get("class")
            school = data.get("school")
            
            from agents.supabase_client import get_supabase
            supabase_admin = get_supabase(use_service_role=True)
            
            # Update user in Supabase
            res = supabase_admin.auth.admin.update_user_by_id(uid, {
                "user_metadata": {
                    "name": name,
                    "dob": dob,
                    "class": student_class,
                    "school": school
                }
            })
            
            # Update local session.json
            session_file = os.path.join(project_root, "session.json")
            if os.path.exists(session_file):
                with open(session_file, "r") as f:
                    session_data = json.load(f)
                session_data["username"] = name
                session_data["dob"] = dob
                session_data["class"] = student_class
                session_data["school"] = school
                with open(session_file, "w") as f:
                    json.dump(session_data, f)
            
            self._send_json({"status": "ok", "user": session_data})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_auth_delete_account(self):
        try:
            uid = self._extract_user()
            if not uid:
                self._send_json({"error": "Unauthorized"}, code=401)
                return
            
            from agents.supabase_client import get_supabase
            supabase_admin = get_supabase(use_service_role=True)
            
            # Delete user files from user_data bucket
            def _delete_storage_recursive(sup, bucket, prefix):
                try:
                    items = sup.storage.from_(bucket).list(prefix)
                    if not items: return
                    files_to_remove = []
                    for item in items:
                        name = item.get("name")
                        if not name: continue
                        if name == ".emptyFolderPlaceholder":
                            files_to_remove.append(f"{prefix}/{name}")
                        elif not item.get("id"):
                            _delete_storage_recursive(sup, bucket, f"{prefix}/{name}")
                        else:
                            files_to_remove.append(f"{prefix}/{name}")
                    if files_to_remove:
                        sup.storage.from_(bucket).remove(files_to_remove)
                except Exception as e:
                    print(f"Failed to delete storage for {prefix}: {e}")
                    
            _delete_storage_recursive(supabase_admin, "user_data", uid)
            
            # Delete user from Supabase
            supabase_admin.auth.admin.delete_user(uid)
            
            # Wipe local memory and session
            import factory_reset
            factory_reset.main()
            
            self._send_json({"status": "deleted"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_upload(self, data):
        import base64
        try:
            files = data.get("files", [])
            if not files:
                self._send_json({"error": "No files provided"}, code=400)
                return
            uploaded_names = []
            input_dir = os.path.join(project_root, "input")
            os.makedirs(input_dir, exist_ok=True)
            for f in files:
                name = f.get("name")
                b64_data = f.get("data")
                if not name or not b64_data:
                    continue
                save_path = os.path.join(input_dir, name)
                try:
                    raw = base64.b64decode(b64_data)
                except Exception as e:
                    print(f"Base64 decode error for {name}: {e}")
                    continue
                try:
                    with open(save_path, "wb") as out:
                        out.write(raw)
                    uploaded_names.append(name)
                except Exception as e:
                    print(f"Write error for {save_path}: {e}")
                    self._send_json({"error": f"Cannot write to {name}: {e}"}, code=500)
                    return
            self._send_json({"status": "ok", "files": uploaded_names})
        except Exception as e:
            print(f"Upload handler error: {e}")
            self._send_json({"error": f"Upload failed: {e}"}, code=500)

    def _handle_scan(self, body):
        try:
            selected_files = body.get("input_files") or body.get("selected_files") or []
            if selected_files:
                response_payload = prescan_selected_files(selected_files)
            else:
                response_payload = prescan_input_files()
            self._send_json(response_payload)
        except Exception as e:
            set_state("error", 0, 0, str(e))
            self._send_json({"error": str(e)}, code=500)

    def _handle_run(self, body):
        global ignore_next_instruction_mtime
        files = body.get("input_files", body.get("files", []))
        if not files:
            self._send_json({"error": "No files provided."}, code=400)
            return
        abs_files = [os.path.join(project_root, "input", f) for f in files]
        for f in abs_files:
            if not os.path.isfile(f):
                self._send_json({"error": f"File not found: {f}"},
                                code=404)
                return
        output_value = body.get("output", "both")
        if isinstance(output_value, dict):
            output_format = output_value.get("format", "both")
        else:
            output_format = output_value
        instruction_data = {
            "input_files": abs_files,
            "student": body.get("student", {}),
            "exam_name": body.get("exam_name"),
            "test_folder": body.get("test_folder"),
            "output": {"format": output_format},
        }
        try:
            import mcp_servers.plot_renderer as _pr
            import mcp_servers.vision_extractor as _ve
            import agents.llmwhisperer_converter as _dc

            def run_sync():
                set_state("running", 0, 0, "Pipeline initialising...")
                try:
                    if body.get("workflow") == "student_report":
                        student_payload = dict(instruction_data)
                        student_payload["workflow"] = "student_report"
                        student_payload["target_exam_name"] = body.get("target_exam_name")
                        run_student_report_pipeline(student_payload)
                    else:
                        run_pipeline(instruction_data)
                    
                    # Store entire local mem and output in user data as requested
                    import agents.local_mem as lm
                    lm.sync_local_to_cloud()
                except Exception as e:
                    set_state("error", 0, 0, str(e))

            t = threading.Thread(target=run_sync, daemon=True)
            t.start()
            self._send_json({"status": "started"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)

    def _handle_stop(self):
        try:
            _cancel_pipeline()
            self._send_json({"status": "stopped"})
        except Exception as e:
            self._send_json({"error": str(e)}, code=500)


def run_student_report_pipeline(instruction_data):
    with pipeline_lock:
        try:
            files = instruction_data.get("input_files", [])
            student_info = instruction_data.get("student", {})
            student_query = (
                student_info.get("query")
                or student_info.get("name")
                or student_info.get("id")
                or ""
            ).strip()

            if not files:
                raise Exception("Select at least 1 report file before generating the student report.")
            if not student_query:
                raise Exception("Enter a student name or ID before generating the student report.")

            set_state("converting", 0, len(files), "Loading student directory...")
            student_record = lm.resolve_student_record(student_query)
            if not student_record and student_info.get("name"):
                student_record = {
                    "student_name": student_info.get("name"),
                    "student_id": student_info.get("id") or student_info.get("name"),
                    "student_class": student_info.get("class", ""),
                    "student_section": student_info.get("section", ""),
                }
            if not student_record:
                raise Exception(
                    f"Student '{student_query}' not found in student directory, and no name was provided manually."
                )

            student_name = str(student_record.get("student_name", student_info.get("name", ""))).strip()
            student_id = str(student_record.get("student_id", student_info.get("id", ""))).strip()
            if not student_name or not student_id:
                raise Exception("Resolved student record is missing a name or ID.")

            target_exam_name = instruction_data.get("exam_name")
            target_test_name = instruction_data.get("test_folder")
            extraction_results = []
            lm.ensure_local_mem_dir()

            for idx, filename in enumerate(files):
                abs_path = filename if os.path.isabs(filename) else os.path.join(project_root, "input", filename)
                wpath = lm.get_whisperer_document_path(abs_path)
                set_state("extracting", idx + 1, len(files), f"Converting and extracting {filename}...", current_file=abs_path, current_markdown=wpath)
                result, _, _ = extract_for_analysis(abs_path, student_name, student_id)
                
                series_name, test_folder, exam_code = lm.parse_report_filename(
                    os.path.basename(filename), target_exam_name
                )
                if target_test_name:
                    test_folder = target_test_name

                ext = os.path.splitext(abs_path)[1]
                target_dir = lm.LOCAL_MEM_DIR / series_name / test_folder
                target_dir.mkdir(parents=True, exist_ok=True)
                source_dest = target_dir / f"Source{ext}"
                try:
                    import shutil
                    shutil.copy2(abs_path, source_dest)
                except Exception as e:
                    print(f"Warning: failed to save Source{ext}: {e}")

                result["source_file"] = os.path.basename(filename)
                result["exam_name"] = series_name
                result["test_name"] = test_folder
                lm.save_phase_3_extraction(
                    series_name,
                    test_folder,
                    exam_code,
                    student_name,
                    student_id,
                    result,
                )
                extraction_results.append(result)
                
                json_path_temp = target_dir / f"{test_folder}.json"
                set_state("extracting", idx + 1, len(files), f"Extracted {os.path.basename(filename)}", current_file=abs_path, current_markdown=wpath, current_jsonpreview=str(json_path_temp))
                import time
                time.sleep(5.0)

            set_state("rendering", len(files), len(files), "Assembling per-student JSON and final report...")
            json_path = lm.maintain_per_student_json(student_name, student_id, json.dumps(extraction_results))
            report_path = lm.render_final_output(student_id, json_path)
            output_format = instruction_data.get("output", {}).get("format", "both")

            if isinstance(json_path, str) and json_path.startswith("Error"):
                raise Exception(json_path)
            if isinstance(report_path, str) and report_path.startswith("Error"):
                raise Exception(report_path)

            generated_files = []
            if isinstance(json_path, str) and not json_path.startswith("Error"):
                generated_files.append(json_path)
            elif isinstance(json_path, Path):
                generated_files.append(str(json_path))

            if output_format in ("markdown", "both"):
                generated_files.append(report_path)

            if output_format in ("charts", "both"):
                full_history = []
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    if isinstance(payload, list):
                        for item in payload:
                            if "prescan" in item:
                                full_history.append(item["prescan"])
                    elif isinstance(payload, dict):
                        full_history.extend(payload.get("results", []))
                except Exception as e:
                    print(f"Warning: Could not load full history for plotting: {e}")
                    full_history = extraction_results

                aggregated = {
                    "student": {"name": student_name, "id": student_id, "class": "", "section": ""},
                    "results": full_history,
                }
                plot_res = pr.render_matplotlib(aggregated, "analyze_instruction.json")
                if plot_res.get("status") == "success":
                    generated_files.extend(plot_res.get("charts", []))

            archive_selected_files([f if os.path.isabs(f) else os.path.join(project_root, "input", f) for f in files])
            clear_input_folder()
            set_state(
                "done",
                len(files),
                len(files),
                f"Student report generated for {student_name}.",
                generated_files,
            )
        except Exception as e:
            print(f"\n❌ Student report pipeline error: {e}")
            import traceback

            traceback.print_exc()
            set_state("error", 0, 0, str(e))


def run_pipeline(instruction_data):
    with pipeline_lock:
        try:
            set_state("running", 0, 0, "Pipeline started.")
            files = instruction_data.get("input_files", [])
            student_info = instruction_data["student"]
            student_name = student_info.get("name", "Unknown")
            student_id = student_info.get("id", "")
            extraction_errors = []
            generated_files = []

            lm.ensure_local_mem_dir()

            # Phase 1: convert PDFs via LLMWhisperer
            set_state("converting", 0, len(files), "Converting files...")
            for idx, f in enumerate(files):
                wpath = lm.get_whisperer_document_path(f)
                set_state("converting", idx + 1, len(files),
                          f"Converting {os.path.basename(f)}...", current_file=f, current_markdown=wpath)
                try:
                    out_dir = lm.get_whisperer_out_dir(f)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    if lm.should_convert_file(f, wpath):
                        dc.convert_file(Path(f), out_dir)
                except Exception as ex:
                    extraction_errors.append(f"{f}: {ex}")

            # Phase 2: build roster
            series_name = instruction_data.get("exam_name") or \
                instruction_data.get("test_folder") or "Unknown"
            lm.run_phase_2_roster(series_name, files)

            # Phase 3: per-file extraction
            for idx, f in enumerate(files):
                wpath = lm.get_whisperer_document_path(f)
                set_state("extracting", idx + 1, len(files),
                          f"Extracting {os.path.basename(f)}...", current_file=f, current_markdown=wpath)
                try:
                    res, file_series, _ = extract_for_analysis(
                        f, student_name, student_id)
                    if file_series and file_series != "Unknown":
                        series_name = file_series
                    
                    if res.get("student_id"):
                        student_id = res.get("student_id")
                        
                    test_folder = res.get("test_name") or "Unknown"
                    ext = os.path.splitext(f)[1]
                    target_dir = lm.LOCAL_MEM_DIR / series_name / test_folder
                    target_dir.mkdir(parents=True, exist_ok=True)
                    source_dest = target_dir / f"Source{ext}"
                    try:
                        import shutil
                        shutil.copy2(f, source_dest)
                    except Exception as e:
                        print(f"Warning: failed to save Source{ext}: {e}")
                        
                    lm.save_phase_3_extraction(
                        series_name=series_name,
                        test_folder=res.get("test_name") or "Unknown",
                        exam_code="",
                        student_name=res.get("student_name") or student_name,
                        student_id=student_id,
                        extraction=res
                    )
                    json_path = target_dir / f"{res.get('test_name') or 'Unknown'}.json"
                    set_state("extracting", idx + 1, len(files),
                              f"Extracted {os.path.basename(f)}", current_file=f, current_markdown=wpath, current_jsonpreview=str(json_path))
                    import time
                    time.sleep(5.0)
                except Exception as ex:
                    extraction_errors.append(f"{f}: {ex}")

            # Phase 4: unified data assembly + render
            print("Assembling Phase 4 unified data for analysis/plotting...")
            unified = lm.run_phase_4_unified_data(series_name, student_id)
            aggregated = lm.map_unified_to_aggregated(unified)

            found_count = sum(1 for t in unified["tests"] if t["found"])
            if found_count == 0:
                raise Exception(
                    f"Failed to find student '{student_name}' in any report."
                    + (" Details: " + "; ".join(extraction_errors)
                       if extraction_errors else "")
                )

            set_state("rendering", len(files), len(files),
                      "Rendering charts and building slides...")
            print("Generating charts and PowerPoint deck...")
            
            # Add the generated JSON files to the deliverables list
            for t in unified.get("tests", []):
                if t.get("found"):
                    jp = lm.LOCAL_MEM_DIR / series_name / t["test_name"] / f'{t["test_name"]}.json'
                    if jp.exists():
                        generated_files.append(str(jp))

            output_format = instruction_data.get(
                "output", {}).get("format", "both")
            instruction_path = "analyze_instruction.json"

            if output_format in ("plotly", "matplotlib", "pptx", "both"):
                print("Rendering Matplotlib charts...")
                plot_res = pr.render_matplotlib(
                    aggregated, instruction_path)
                if output_format in ("plotly", "matplotlib", "both") \
                        and plot_res.get("status") == "success":
                    generated_files.extend(plot_res.get("charts", []))

            if output_format in ("pptx", "both"):
                print("Building PowerPoint Presentation...")
                pptx_res = pr.render_pptx(aggregated, instruction_path)
                if pptx_res.get("status") == "success":
                    generated_files.append(pptx_res.get("file"))

            completion_message = "Analysis Complete!"
            if extraction_errors:
                completion_message += " Some files were skipped."
            set_state("done", len(files), len(files),
                      completion_message, generated_files)

            archived = archive_selected_files(files)
            if archived:
                print("Archived input files:")
                for a in archived:
                    print(f" - {a}")

            clear_input_folder()

            print("=== ANALYSIS COMPLETE ===")
            for g in generated_files:
                print(f" - {g}")

            if extraction_errors:
                print("Skipped files / warnings:")
                for msg in extraction_errors:
                    print(f" - {msg}")

        except Exception as e:
            print(f"\n===== PIPELINE ERROR: {e} =====")
            import traceback
            traceback.print_exc()
            set_state("error", 0, 0, str(e))


def _cancel_pipeline():
    set_state("idle", 0, 0, "Pipeline stopped.")


# ─── RAG endpoint handlers ────────────────────────────────────────────────────

def _handle_rag_list(self):
       """GET /api/rag/textbooks — list all ingested textbooks."""
       try:
           result = rag.list_textbooks()
           self._send_json(result)
       except Exception as e:
           self._send_json({"error": str(e)}, code=500)

def _handle_rag_health(self):
       """GET /api/rag/health — quick health check for Pinecone + NVIDIA NIM."""
       try:
           result = rag.health_check()
           self._send_json(result)
       except Exception as e:
           self._send_json({"error": str(e)}, code=500)

def _handle_rag_post(self, body):
       """POST /api/rag/* — dispatches ingest / query actions."""
       if not body:
           self._send_json({"error": "Empty body"}, code=400)
           return
       if isinstance(body, dict):
           payload = body
       else:
           try:
               payload = json.loads(body)
           except Exception:
               self._send_json({"error": "Invalid JSON"}, code=400)
               return

       action = payload.get("action", "")
       if action == "ingest":
           self._handle_rag_ingest(payload)
       elif action == "query":
           self._handle_rag_query(payload)
       elif action == "delete":
           self._handle_rag_delete(payload)
       elif action == "list":
           self._handle_rag_list()
       else:
           self._send_json({"error": f"Unknown RAG action: {action}"}, code=400)

def _handle_rag_ingest(self, payload):
       """POST /api/rag — action=ingest: process a PDF upload."""
       file_b64      = payload.get("file_bytes_b64", "")
       textbook_name = payload.get("textbook_name", "")
       file_name     = payload.get("file_name", "textbook.pdf")
       if not file_b64 or not textbook_name:
           self._send_json({"error": "file_bytes_b64 and textbook_name are required"}, code=400)
           return
       try:
           result = rag.ingest_textbook(file_bytes_b64=file_b64,
                                         textbook_name=textbook_name,
                                         file_name=file_name)
           if result.get("status") == "error":
               self._send_json(result, code=500)
           else:
               self._send_json(result)
       except Exception as e:
           self._send_json({"status": "error", "error": str(e)}, code=500)

def _handle_rag_query(self, payload):
       """POST /api/rag — action=query: ask a question against a textbook."""
       textbook_name = payload.get("textbook_name", "")
       question      = payload.get("question", "")
       top_k         = int(payload.get("top_k", 4))
       username      = payload.get("username")
       if not textbook_name or not question:
           self._send_json({"error": "textbook_name and question are required"}, code=400)
           return
       if not username:
           try:
               session_file = os.path.join(project_root, "session.json")
               if os.path.exists(session_file):
                   with open(session_file, "r") as f:
                       session_data = json.load(f)
                   username = session_data.get("username")
           except Exception:
               pass
       try:
           result = rag.query_textbook(textbook_name=textbook_name,
                                        question=question,
                                        top_k=top_k,
                                        username=username)
           if result.get("status") == "error":
               self._send_json(result, code=500)
           else:
               self._send_json(result)
       except Exception as e:
           self._send_json({"status": "error", "error": str(e)}, code=500)

def _handle_rag_delete(self, payload):
       """POST /api/rag — action=delete: remove a textbook from Pinecone."""
       textbook_name = payload.get("textbook_name", "")
       if not textbook_name:
           self._send_json({"error": "textbook_name is required"}, code=400)
           return
       try:
           result = rag.delete_textbook(textbook_name)
           self._send_json(result)
       except Exception as e:
           self._send_json({"status": "error", "error": str(e)}, code=500)

OrchestratorHTTPHandler._handle_rag_list = _handle_rag_list
OrchestratorHTTPHandler._handle_rag_health = _handle_rag_health
OrchestratorHTTPHandler._handle_rag_post = _handle_rag_post
OrchestratorHTTPHandler._handle_rag_ingest = _handle_rag_ingest
OrchestratorHTTPHandler._handle_rag_query = _handle_rag_query
OrchestratorHTTPHandler._handle_rag_delete = _handle_rag_delete

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--watch", action="store_true", help="Enable watch mode")
    args = parser.parse_args()

    port = args.port
    max_attempts = 100
    server = None

    for attempt in range(max_attempts):
        try:
            server = ThreadingHTTPServer((args.host, port),
                                         OrchestratorHTTPHandler)
            break
        except (PermissionError, OSError) as e:
            print(f"Port {port} not available: {e}. Trying next port...")
            port += 1

    if not server:
        print(f"Error: Could not bind to any port in the range {args.port} to {args.port + max_attempts - 1}.", file=sys.stderr)
        sys.exit(1)

    lm.ensure_local_mem_dir()
    try:
        with open(os.path.join(project_root, "local_mem", "port.txt"), "w") as f:
            f.write(str(port))
    except Exception as e:
        print(f"Warning: Could not write port to file: {e}")

    display_host = "localhost" if args.host == "0.0.0.0" else args.host
    print(f"Serving on http://{display_host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            port_file = os.path.join(project_root, "local_mem", "port.txt")
            if os.path.exists(port_file):
                os.remove(port_file)
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()

