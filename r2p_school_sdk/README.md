# R2P School SDK

One-liner Python SDK for plugging R2P-Enterprise into any school app or website.

## Install

```bash
pip install r2p-school-sdk
```

Or for local dev:

```bash
cd /path/to/R2P-Enterprise/r2p_school_sdk
pip install -e .
```

## Quickstart

```python
from r2p_school_sdk import R2PSchoolClient

client = R2PSchoolClient(
    api_url="https://r2p-enterprise.onrender.com",
    api_key="sk-<student-or-school-key>",
)

result = client.upload_report(
    file_path="report.pdf",
    student_name="Aarav Sharma",
    student_id="grade10-aarav",
    output_format="pptx",
)
print(result)
```

## Per-student RAG namespace

Each student gets a private Pinecone namespace in the school's index, so
`query_textbook` always returns results from that student's own ingested
materials only:

```python
client.query_textbook(
    textbook_name="grade10-biology",
    question="What is photosynthesis?",
    student_id="grade10-aarav",  # isolated namespace
    top_k=5,
)
```
