"""
Runner script for Phase 7 Strategy Robustness & Execution Reality Experiment.
Executes all 13 experiments and writes all 14 deliverables (13 CSVs + 1 master JSON).
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

workspace_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(workspace_dir / "engine" / "src"))

from quantedge.ai.research.strategy_robustness_experiment import run_all_robustness_experiments

OUT = workspace_dir / "docs" / "ai"

if __name__ == "__main__":
    print("=" * 70)
    print("PHASE 7 — STRATEGY ROBUSTNESS & EXECUTION REALITY EXPERIMENT")
    print("=" * 70)
    results = run_all_robustness_experiments(output_dir=OUT)
    print("\nAll 13 experiments completed successfully.")
    print("Deliverables written to docs/ai/")
