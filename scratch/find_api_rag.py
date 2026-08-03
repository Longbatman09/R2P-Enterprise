import os

root_dir = r"C:\Users\dhana\Desktop\Final\R2P_OLAP_-Online-Analytical-Processing--main\R2P_OLAP_-Online-Analytical-Processing--main"
for root, dirs, files in os.walk(root_dir):
    # Skip standard python / git dirs
    if any(x in root for x in [".git", "__pycache__", ".agents"]):
        continue
    for file in files:
        if file.endswith((".py", ".js", ".json", ".html", ".bat", ".md")):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if "/api/rag" in content or "api/rag" in content:
                    print(f"Found in: {os.path.relpath(filepath, root_dir)}")
            except Exception as e:
                pass
