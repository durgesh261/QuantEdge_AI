"""
QuantEdge AI — Phase K Research Runner.

Executes the expanded historical OB research pipeline, power calculations,
pre-registered model benchmark, LOAO, cost stress testing, and generates all reports.
"""

from __future__ import annotations

from pathlib import Path
from quantedge.ai.evaluation.phase_k_reports import write_all_phase_k_reports
from quantedge.ai.evaluation.phase_k_research import (
    PhaseKResearchPipeline,
    build_phase_k_dataset,
)


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


def run_phase_k_pipeline():
    repo_root = _find_repo_root()
    canonical_base = repo_root / "data" / "canonical" / "delta_exchange_india"

    print("=" * 70)
    print("  QuantEdge AI — Phase K: Expanded Historical Sample & Power Analysis")
    print("=" * 70)

    print("\n[Phase K] Building expanded historical dataset from canonical candles...")
    df = build_phase_k_dataset(canonical_base)
    print(f"[Phase K] Successfully built dataset with {len(df)} total unique OB setups.")

    print("\n[Phase K] Running research pipeline...")
    pipeline = PhaseKResearchPipeline(df)
    results = pipeline.run_all()

    print("\n[Phase K] Generating all comprehensive reports...")
    output_dir = repo_root / "docs" / "ai"
    write_all_phase_k_reports(results, output_dir)

    print("\n" + "=" * 70)
    print(f"  PHASE K COMPLETE")
    print(f"  OOS Incremental Expectancy: {results['primary_oos_results']['incremental_expectancy_r']:+.4f}R")
    print(f"  Paired MBB 95% CI: [{results['bootstrap_ci']['incremental_95ci'][0]:+.4f}R, {results['bootstrap_ci']['incremental_95ci'][1]:+.4f}R]")
    print(f"  Promotion Gate Status: {results['promotion_gate']['status']}")
    print("=" * 70)

    return results


if __name__ == "__main__":
    run_phase_k_pipeline()
