# R2P School SDK

"""R2P School SDK — one-liner integration for schools.

Quickstart:
    from r2p_school_sdk import R2PSchoolClient

    client = R2PSchoolClient(
        api_url="https://r2p-enterprise.onrender.com",
        api_key="sk-...",
    )
    result = client.upload_report("report.pdf", "Aarav", "10-A")
"""

from .client import R2PSchoolClient
from .utils import file_to_base64

__all__ = ["R2PSchoolClient", "file_to_base64"]
__version__ = "0.1.0"
