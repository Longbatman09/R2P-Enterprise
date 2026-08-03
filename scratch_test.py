import json
from agents.local_mem import maintain_per_student_json

student_name = 'B Vishal Chandrakanth'
student_id = 'B Vishal Chandrakanth'

# Mock a new upload
new_upload = [{
    'exam_name': 'Jee Mains WTM',
    'test_name': 'WTM 33',
    'numerical_fields': {'physics': 85},
    'student_name': student_name,
    'student_id': student_id
}]

json_path = maintain_per_student_json(student_name, student_id, json.dumps(new_upload))
print('Saved JSON path:', json_path)

with open(json_path, 'r') as f:
    data = json.load(f)
    results = data.get('results', [])
    print('Total results in JSON:', len(results))
    for r in results:
        print(f"- {r.get('exam_name')} / {r.get('test_name')}")
