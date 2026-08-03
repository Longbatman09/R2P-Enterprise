import sys
import os
import json
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent))

import agents.orchestrator as orch
import agents.local_mem as lm

print("=== STEP 1 & 2: Pre-scan files and create All_stud_details.json ===")
try:
    prescan_payload = orch.prescan_input_files()
    print("Pre-scan complete. Discovered students:")
    students = lm.load_student_directory()
    for s in students:
        print(f"  - {s.get('student_name')} (ID: {s.get('student_id')})")
        
    if not students:
        print("No students discovered. Exiting.")
        sys.exit(1)
        
    # Select the first student
    selected_student = students[0]
    print(f"\n=== STEP 3 & 4: Select student '{selected_student.get('student_name')}' and generate plots ===")
    
    instruction_data = {
        "workflow": "student_report",
        "input_files": ["WTM 29.pdf", "WTM 30.pdf"],
        "student": {
            "name": selected_student.get("student_name"),
            "id": selected_student.get("student_id")
        },
        "target_exam_name": "Jee Mains WTM",
        "output": {
            "format": "both"
        }
    }
    
    orch.run_student_report_pipeline(instruction_data)
    print("\n=== PIPELINE EXECUTION COMPLETE ===")
    
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"Error during execution: {e}")
