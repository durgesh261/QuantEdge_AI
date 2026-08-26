"""
QuantEdge AI — Position Sizing Experiment Runner.
Executes all 6 position sizing experiments and generates CSV/JSON deliverables in docs/ai/.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

# Add engine src to path
workspace_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(workspace_dir / "engine" / "src"))

from quantedge.ai.research.position_sizing_experiment import run_all

OUT = workspace_dir / "docs" / "ai"

if __name__ == "__main__":
    print(f"Running 6-part position sizing experiment suite...")
    results = run_all(output_dir=OUT, n_mc_sims=10_000)
    print("Execution complete. All deliverables generated.")
