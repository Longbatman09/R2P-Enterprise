import time

with open('E:/R2P_OLAP_-Online-Analytical-Processing-/mcp_servers/plot_renderer.py', 'r') as f:
    content = f.read()

content = content.replace('img_path = OUTPUT_DIR / f"{dt_name}.png"', 'ts = int(time.time())\n        img_path = OUTPUT_DIR / f"{dt_name}_{ts}.png"')
content = content.replace('summary_img = OUTPUT_DIR / "overall_summary.png"', 'ts = int(time.time())\n        summary_img = OUTPUT_DIR / f"overall_summary_{ts}.png"')

with open('E:/R2P_OLAP_-Online-Analytical-Processing-/mcp_servers/plot_renderer.py', 'w') as f:
    f.write(content)
