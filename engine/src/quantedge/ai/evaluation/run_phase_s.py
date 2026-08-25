"""
QuantEdge AI — Phase S Robustness & Generalization Audit Runner CLI.

Usage:
    python -m quantedge.ai.evaluation.run_phase_s
"""

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

from quantedge.ai.evaluation.phase_s_robustness import (
    PhaseSRobustnessAudit,
    _find_repo_root,
    write_phase_s_artifacts,
)


def main() -> int:
    repo_root = _find_repo_root()
    master_csv = repo_root / "docs" / "ai" / "2026_smc_order_blocks_master.csv"

    print("=" * 80)
    print("  QuantEdge AI — Phase S: AI Filter Robustness & Generalization Audit")
    print("=" * 80)
    print()

    if not master_csv.exists():
        print(f"[ERROR] Master dataset missing at {master_csv}!")
        return 1

    print(f"[Phase S] Loading 2026 Master Dataset from: {master_csv}")
    master_df = pd.read_csv(master_csv)
    print(f"[Phase S] Loaded {len(master_df)} qualified 2026 Order Blocks.")

    print("[Phase S] Initializing Robustness Audit Suite...")
    audit = PhaseSRobustnessAudit(master_df)

    print("[Phase S] Executing Moving Block Bootstrap, Random Benchmarks, and Diagnostic Audits...")
    results = audit.run_audit()

    cls = results["evidence_classification"]
    boot = results["moving_block_bootstrap"]
    r_bm = results["random_coverage_benchmark"]
    econ = results["economic_analysis"]

    print("\n" + "=" * 80)
    print("  PHASE S AUDIT RESULTS & EVIDENCE CLASSIFICATION")
    print("=" * 80)
    print(f"Overall Classification:       {cls['classification']}")
    print(f"Summary:                      {cls['summary']}")
    print()
    print("Moving Block Bootstrap (10,000 resamples):")
    print(f"  SMC Expectancy 95% CI:      [{boot['smc_expectancy_95ci'][0]:+.4f}R, {boot['smc_expectancy_95ci'][1]:+.4f}R]")
    print(f"  AI Expectancy 95% CI:       [{boot['ai_expectancy_95ci'][0]:+.4f}R, {boot['ai_expectancy_95ci'][1]:+.4f}R]")
    print(f"  Incremental Delta 95% CI:   [{boot['incremental_expectancy_95ci'][0]:+.4f}R, {boot['incremental_expectancy_95ci'][1]:+.4f}R]")
    print(f"  Probability Delta > 0:      {boot['p_value_incremental_greater_than_zero']*100:.1f}%")
    print()
    print("Coverage-Matched Random Benchmark (10,000 resamples):")
    print(f"  Ridge Realized Expectancy:  {r_bm['ridge_realized_expectancy']:+.4f}R")
    print(f"  Random Benchmark Mean Exp:  {r_bm['random_benchmark_mean_exp']:+.4f}R (95% CI: [{r_bm['random_benchmark_95ci'][0]:+.4f}R, {r_bm['random_benchmark_95ci'][1]:+.4f}R])")
    print(f"  Ridge Percentile in Random: {r_bm['ridge_percentile_rank_in_random_distribution']:.1f}th Percentile (p = {r_bm['empirical_p_value_ridge_vs_random']:.4f})")
    print()
    print("Conservative 1.0% Fixed-Fractional Economic Growth ($10,000 Start):")
    print(f"  SMC Baseline Return:        {econ['smc_net_return_pct']:+.2f}% (Max Drawdown: {econ['smc_max_drawdown_pct']:.2f}%)")
    print(f"  AI Filtered Return:         {econ['ai_net_return_pct']:+.2f}% (Max Drawdown: {econ['ai_max_drawdown_pct']:.2f}%)")
    print()

    print("[Phase S] Writing artifacts and report...")
    artifacts = write_phase_s_artifacts(results, repo_root=repo_root)
    for name, path in artifacts.items():
        print(f"  Written {name}: {path}")

    print("\n" + "=" * 80)
    print("  PHASE S AUDIT COMPLETE & DETERMINISTIC")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
