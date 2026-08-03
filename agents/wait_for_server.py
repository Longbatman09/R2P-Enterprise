"""Wait until the orchestrator HTTP server responds."""

import os
import sys
import time
import urllib.error
import urllib.request

MAX_ATTEMPTS = 45
SLEEP_SECONDS = 0.5


def find_server_url() -> str | None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    port_file = os.path.join(project_root, "local_mem", "port.txt")

    for _ in range(MAX_ATTEMPTS):
        port = 8080  # fallback default
        if os.path.exists(port_file):
            try:
                with open(port_file, "r") as f:
                    port = int(f.read().strip())
            except Exception:
                pass
        
        # Check the specific port (both IPv4 loopback and localhost)
        for host in ("127.0.0.1", "localhost"):
            url = f"http://{host}:{port}/api/state"
            try:
                with urllib.request.urlopen(url, timeout=1.5) as response:
                    if response.status == 200:
                        return f"http://{host}:{port}/login_page.html"
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
                
        time.sleep(SLEEP_SECONDS)
    return None


def main() -> int:
    server_url = find_server_url()
    if server_url:
        print(server_url)
        return 0
    print("Orchestrator did not become ready.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
