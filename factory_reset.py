import os
import shutil
import json

def clear_directory(dir_path):
    """Deletes all files and folders inside the given directory, then recreates it if needed."""
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
        print(f"Created directory: {dir_path}")
        return
        
    for item in os.listdir(dir_path):
        item_path = os.path.join(dir_path, item)
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
        except Exception as e:
            print(f"Failed to delete {item_path}. Reason: {e}")
    print(f"Cleared contents of directory: {dir_path}")

def reset_json_file(file_path, default_content):
    """Resets a JSON file to its default content."""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, indent=4)
        print(f"Reset file: {file_path}")
    except Exception as e:
        print(f"Failed to reset {file_path}. Reason: {e}")

def main():
    print("=== Report2Plot Factory Reset ===")
    print("This will clear all generated reports, extracted local memory, and student data.")
    
    # Define paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    local_mem_dir = os.path.join(base_dir, "Local_Mem")
    local_mem_lower_dir = os.path.join(base_dir, "local_mem")
    output_dir = os.path.join(base_dir, "Output")
    input_dir = os.path.join(base_dir, "input")
    archived_dir = os.path.join(base_dir, "Archived_Files")
    
    all_stud_details = os.path.join(base_dir, "All_stud_details.json")
    analyze_instruction = os.path.join(base_dir, "analyze_instruction.json")
    session_file = os.path.join(base_dir, "session.json")
    
    # 1. Clear Data Directories
    clear_directory(local_mem_dir)
    clear_directory(local_mem_lower_dir)
    clear_directory(output_dir)
    clear_directory(input_dir)  # Optional, but ensures a perfectly clean slate
    clear_directory(archived_dir)
    
    # 2. Reset JSON Database & State Files
    reset_json_file(all_stud_details, [])
    reset_json_file(analyze_instruction, {})
    
    # 3. Clear Login Credentials
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
            print(f"Deleted file: {session_file}")
        except Exception as e:
            print(f"Failed to delete {session_file}. Reason: {e}")
    
    print("\nFactory reset complete! The software is now clean and ready for a new user.")

if __name__ == "__main__":
    main()
