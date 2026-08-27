"""
Extract detailed 2026 monthly breakdown for all 4 pairs under Fixed 0.60% TP.
"""

from pathlib import Path
import pandas as pd

from quantedge.ai.evaluation.phase_l_research import _find_repo_root

root = _find_repo_root()
docs_ai_dir = root / "docs" / "ai"

# Load year 2026 summary and trades
df_sum = pd.read_csv(docs_ai_dir / "year_2026_fixed_06_tp_summary.csv")

print("=" * 80)
print("YEAR 2026 FULL SUMMARY")
print("=" * 80)
print(df_sum.to_string(index=False))
