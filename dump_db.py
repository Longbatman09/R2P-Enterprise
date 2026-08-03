from agents.supabase_client import get_supabase
import json

sup = get_supabase()
res = sup.table('student_reports').select('exam_name, test_name, student_id, data').execute()
print(f'Total records: {len(res.data)}')
for row in res.data:
    print(f'Exam: {row.get("exam_name")} | Test: {row.get("test_name")} | Student: {row.get("student_id")}')
    if row.get("exam_name") == "all_aggregated":
        data = row.get("data", {})
        results = data.get("results", [])
        print(f'  -> Aggregated results count: {len(results)}')
        for r in results:
            print(f'     - {r.get("exam_name")} / {r.get("test_name")}')
