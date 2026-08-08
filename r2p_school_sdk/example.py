"""Drop-in example for a school app.

FastAPI route, Django view, or plain script — all use the same two lines.
"""

from r2p_school_sdk import R2PSchoolClient

client = R2PSchoolClient(
    api_url="https://r2p-enterprise.onrender.com",
    api_key="sk-...",  # created in the dashboard → School → API Keys
)


def analyze_student_report(file_path: str, student_name: str, student_id: str) -> dict:
    """Full pipeline: upload → extract → charts → PPTX. Blocks until done."""
    return client.upload_report(
        file_path=file_path,
        student_name=student_name,
        student_id=student_id,
        output_format="pptx",
        wait=True,
    )


def answer_question(textbook: str, question: str, student_id: str, top_k: int = 5) -> dict:
    """Per-student RAG — only returns content the student actually has."""
    return client.query_textbook(
        textbook_name=textbook,
        question=question,
        student_id=student_id,
        top_k=top_k,
    )


def index_study_material(pdf_path: str, textbook_name: str, student_id: str) -> dict:
    """Index a student's own study material into their private namespace."""
    return client.ingest_textbook(
        file_path=pdf_path,
        textbook_name=textbook_name,
        student_id=student_id,
    )
