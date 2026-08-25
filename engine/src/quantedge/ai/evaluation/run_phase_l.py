"""
QuantEdge AI — Phase L Research Runner.

Executes the powered chronological OOS validation, power curves,
10,000-resample paired bootstrap, LOAO, walk-forward, and produces all 10 reports.
"""

from __future__ import annotations

from pathlib import Path
from quantedge.ai.evaluation.phase_l_reports import write_all_phase_l_reports
from quantedge.ai.evaluation.phase_l_research import (
    PhaseLResearchPipeline,
    build_phase_l_dataset,
)


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


def run_phase_l_pipeline():
    repo_root = _find_repo_root()
    canonical_base = repo_root / "data" / "canonical" / "delta_exchange_india"

    print("=" * 70)
    print("  QuantEdge AI — Phase L: Powered Chronological OOS Research")
    print("=" * 70)

    print("\n[Phase L] Building full historical dataset from canonical candles...")
    df = build_phase_l_dataset(canonical_base)
    print(f"[Phase L] Successfully built dataset with {len(df)} total unique OB setups.")

    print("\n[Phase L] Running powered confirmatory research pipeline...")
    pipeline = PhaseLResearchPipeline(df)
    results = pipeline.run_all()

    print("\n[Phase L] Generating all comprehensive reports...")
    output_dir = repo_root / "docs" / "ai"
    write_all_phase_l_reports(results, output_dir)

    print("\n" + "=" * 70)
    print(f"  PHASE L COMPLETE")
    print(f"  Powered OOS Incremental Expectancy: {results['primary_confirmatory_oos']['incremental_expectancy_r']:+.4f}R")
    print(f"  10,000-Resample Paired MBB 95% CI: [{results['primary_confirmatory_oos']['bootstrap_95ci']['incremental_95ci'][0]:+.4f}R, {results['primary_confirmatory_oos']['bootstrap_95ci']['incremental_95ci'][1]:+.4f}R]")
    print(f"  Promotion Gate Status: {results['promotion_gate']['status']}")
    print("=" * 70)

    return results


if __name__ == "__main__":
    run_phase_l_pipeline()
