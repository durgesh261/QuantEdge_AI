"""
Check all win rates across all reports in docs/ai.
"""

from pathlib import Path
import json
import re

docs_ai = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI\docs\ai")

for p in docs_ai.glob("*.md"):
    content = p.read_text(encoding="utf-8", errors="ignore")
    matches = re.findall(r"(\b[0-9]{2}\.[0-9]{1,2}%\b|\b77[0-9.%]*\b)", content)
    for m in matches:
        if "77" in m or "78" in m or "76" in m:
            print(f"[{p.name}] found: {m}")

for p in docs_ai.glob("*.json"):
    content = p.read_text(encoding="utf-8", errors="ignore")
    if "77." in content or "0.77" in content:
        print(f"[JSON: {p.name}] matches 77")
