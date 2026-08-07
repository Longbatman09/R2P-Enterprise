# R2P-Enterprise Project Workflow

## 1) Project Goal
R2P-Enterprise processes academic report files (PDF/images), extracts structured student performance data with AI, and generates analysis artifacts (JSON, charts, and optional presentation/report outputs).  
The system also includes authenticated API access, user data sync, and textbook RAG support.

## 2) Core Components
- **UI (`UI/*.html`)**: Login, dashboard, history, exam details, settings, RAG chat.
- **Orchestrator (`agents/orchestrator.py`)**: HTTP server for upload/scan/analyze flows and state tracking.
- **MCP Backend (`mcp_backend/main.py`)**: FastAPI JSON-RPC + SSE server exposing tool modules.
- **Extraction/Render Modules (`mcp_servers/*`)**:
  - `vision_extractor.py` for report data extraction
  - `plot_renderer.py` for chart/presentation rendering
  - `rag_system.py` for textbook ingest/query/delete
  - `file_watcher.py` and `ui_orchestrator.py` for support orchestration
- **Local Memory (`agents/local_mem.py`)**: Persistent local cache/history and phase outputs.
- **Auth Layer (`mcp_backend/auth_routes.py`, `mcp_backend/auth_supabase.py`)**: Signup/login/JWT and encrypted API key management.

## 3) End-to-End Student Report Workflow
### A. User Session & Context
1. User signs up/logs in.
2. Session context is established (token/session data).
3. User profile and cloud-linked data are synchronized to local memory where applicable.

### B. File Intake
1. User uploads report files through orchestrator endpoints.
2. Files are validated for supported extensions.
3. Input files are staged in the project input area.

### C. Pre-Scan Phase
1. System lists pending input files.
2. Each file is converted to markdown/text form (via converter flow) when needed.
3. `vision_extractor` reads converted content and extracts:
   - exam/test metadata
   - student identity fields
   - numerical performance fields
4. Results are cached in local memory to avoid repeat work.
5. A pre-scan payload is assembled with common fields/students and selected file set.

### D. Analysis Pipeline
1. Pipeline starts with state/progress updates.
2. Phase 1: Conversion pass for all selected files.
3. Phase 2: Roster preparation for the exam/series.
4. Phase 3: Per-file extraction and normalized JSON generation.
5. Phase 4: Unified student data assembly across tests.
6. Rendering:
   - charts (Matplotlib output)
   - optional PPTX/report outputs depending on requested format

### E. Output Finalization
1. Generated artifacts are recorded in pipeline state.
2. Source files are archived (`Archived_Files` with timestamped names).
3. Input staging folder is cleared.
4. Final status is set to `done` (or `error` on failure).

## 4) Runtime API Workflow (Orchestrator)
- **State/health style operations**
  - `GET /api/state` for pipeline progress
  - `GET /api/heartbeat` for active client heartbeat tracking
- **Auth/session operations**
  - `POST /api/auth/signup`
  - `POST /api/auth/login`
  - `POST /api/auth/logout`
  - `GET /api/auth/session`
- **Pipeline operations**
  - `POST /api/upload` to stage files
  - `POST /api/scan` to pre-scan selected inputs
  - `POST /api/analyze` or `POST /api/run` to execute analysis
  - `POST /api/stop` to cancel/reset state
- **Data/history operations**
  - `GET /api/history`
  - `POST /api/report`
  - `GET/POST /api/roster`
  - `GET/POST /api/profile`
- **RAG operations**
  - `GET /api/rag/textbooks`
  - `GET /api/rag/health`
  - `POST /api/rag` with actions: `ingest`, `query`, `delete`, `list`

## 5) MCP Backend Workflow
1. Client authenticates with JWT.
2. Client calls `POST /mcp` using JSON-RPC:
   - `initialize`
   - `tools/list`
   - `tools/call`
   - `resources/list`
3. Backend lazily loads tool modules from registry and dispatches calls.
4. SSE stream (`GET /sse`) broadcasts MCP response events for live client updates.

## 6) Data & Storage Flow
- **Input**: user-uploaded report files
- **Intermediate**:
  - converted markdown/text
  - pre-scan cache entries
  - per-test JSON extraction artifacts
- **Output**:
  - unified student analysis payloads
  - charts
  - optional report/presentation files
- **Persistence**:
  - local memory directories for cached and generated data
  - optional cloud sync/auth/key storage through Supabase

## 7) Error Handling Workflow
- Per-file extraction failures are captured and tracked without necessarily aborting the entire run.
- Full-run failure occurs when no valid extraction is available or critical phases fail.
- Pipeline state transitions to `error` with the failure reason.
- Successful completion can still include warnings for skipped/invalid files.

## 8) Development & Operations Workflow
1. Configure environment variables (`mcp_backend/.env.local` or equivalent).
2. Run locally using Docker Compose (`mcp_backend` service).
3. Validate service health (`/health`) before front-end/API operations.
4. Use history/state endpoints to monitor live pipeline behavior.
5. Deploy backend via Docker runtime (as documented in `DEPLOY.md`), then validate auth + MCP + RAG endpoints.

## 9) High-Level Lifecycle Summary
**Authenticate → Upload → Pre-scan → Analyze (convert/extract/unify/render) → Archive/Clear input → Retrieve outputs/history → Iterate.**
