import base64
from pathlib import Path


def file_to_base64(path: str) -> str:
    p = Path(path)
    return base64.b64encode(p.read_bytes()).decode()
