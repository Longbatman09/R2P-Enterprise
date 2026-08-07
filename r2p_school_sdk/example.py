"""Drop-in example for a school app.

FastAPI route, Django view, or plain script — all use the same two lines.
"""

from r2p_school_sdk import R2PSchoolClient

client = R2PSchoolClient(
    api_url="https://r2p-enterprise.onrender.com",
    api_key="sk-school-api-key",
)


def analyze_student_report(file_path: str, student_name: str, student_id: str) -> dict:
    result = client.upload_report(
        file_path=file_path,
        student_name=student_name,
        student_id=student_id,
        output_format="pptx",
    )
    return result


def answer_question(textbook: str, question: str, student_id: str, top_k: int = 5) -> dict:
    return client.query_textbook(
        textbook_name=textbook,
        question=question,
        student_id=student_id,
        top_k=top_k,
    )
