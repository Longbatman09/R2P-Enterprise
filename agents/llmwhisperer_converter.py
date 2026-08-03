import os
import sys
import time
import requests
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

load_dotenv()

LLMWHISPERER_URL = os.environ.get(
    "LLMWHISPERER_URL",
    "https://llmwhisperer-api.us-central.unstract.com/api/v2",
)
LLMWHISPERER_API_KEY = os.environ.get("LLMWHISPERER_API_KEY")

def _headers() -> dict:
    if not LLMWHISPERER_API_KEY:
        raise RuntimeError("LLMWHISPERER_API_KEY is not set. Add it to your .env file.")
    return {
        "unstract-key": LLMWHISPERER_API_KEY,
    }


def convert_file(file_path: Path, output_dir: Path, filename: str = "") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = filename if filename else file_path.stem
    output_path = output_dir / f"{stem}.md"

    print(f"Converting {file_path.name} via LLMWhisperer...")

    # Step 1: submit binary document (application/octet-stream)
    with open(file_path, "rb") as fh:
        file_bytes = fh.read()

    whisper_url = (
        f"{LLMWHISPERER_URL}/whisper"
        "?mode=form&output_mode=layout_preserving"
    )
    resp = requests.post(
        whisper_url,
        headers=_headers(),
        data=file_bytes,
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()
    whisper_hash = result.get("whisher_hash") or result.get("whisper_hash")
    if not whisper_hash:
        raise RuntimeError(f"No whisper_hash in response: {result}")

    # Step 2: poll status until processed
    status_url = f"{LLMWHISPERER_URL}/whisper-status?whisper_hash={whisper_hash}"
    for _ in range(60):
        status_resp = requests.get(status_url, headers=_headers(), timeout=30)
        status_resp.raise_for_status()
        status_data = status_resp.json().get("status")  # "processing" / "processed"
        if status_data == "processed":
            break
        time.sleep(2)
    else:
        raise RuntimeError("LLMWhisperer timed out waiting for processing.")

    # Step 3: retrieve extracted text
    retrieve_url = f"{LLMWHISPERER_URL}/whisper-retrieve?whisper_hash={whisper_hash}"
    retr_resp = requests.get(retrieve_url, headers=_headers(), timeout=60)
    retr_resp.raise_for_status()
    retr_data = retr_resp.json()

    markdown_content = retr_data.get("extracted_text") or retr_data.get("result_text") or retr_data.get("text") or retr_data.get("markdown") or ""
    if not markdown_content:
        raise RuntimeError(f"LLMWhisperer returned empty content: {retr_data}")

    output_path.write_text(markdown_content, encoding="utf-8")
    print(f"Saved: {output_path.name}")
    return output_path


def convert_all_inputs():
    from agents.local_mem import get_whisperer_out_dir
    local_mem_dir = project_root / "local_mem"
    input_dir = project_root / "input"

    if not input_dir.exists():
        print("Input directory does not exist.")
        return

    supported_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".txt"}
    files_to_convert = [
        f for f in input_dir.iterdir()
        if f.suffix.lower() in supported_extensions and f.is_file()
    ]

    if not files_to_convert:
        print("No supported input files found to convert.")
        return

    print(f"Found {len(files_to_convert)} files to convert.")
    for file_path in files_to_convert:
        try:
            output_dir = get_whisperer_out_dir(file_path.name)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{file_path.stem}.md"
            if output_path.exists() and output_path.stat().st_mtime >= file_path.stat().st_mtime:
                print(f"Skipping (up-to-date): {file_path.name}")
                continue
            convert_file(file_path, output_dir)
            from agents.local_mem import upload_file_to_storage, parse_report_filename
            import threading
            series, test, _ = parse_report_filename(file_path.name)
            threading.Thread(target=upload_file_to_storage, args=(str(output_path), f"{series}/{test}/whispery_out/{output_path.name}"), daemon=True).start()
        except Exception as e:
            print(f"Error converting {file_path.name}: {e}")


if __name__ == "__main__":
    convert_all_inputs()
