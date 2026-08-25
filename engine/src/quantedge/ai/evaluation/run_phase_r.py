"""
QuantEdge AI — Phase R Walk-Forward Research Runner CLI.

Usage:
    python -m quantedge.ai.evaluation.run_phase_r
"""

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

from quantedge.ai.evaluation.phase_r_walk_forward import (
    PhaseRWalkForwardPipeline,
    _find_repo_root,
    write_phase_r_artifacts,
)


def main() -> int:
    repo_root = _find_repo_root()
    master_csv = repo_root / "docs" / "ai" / "2026_smc_order_blocks_master.csv"

    print("=" * 80)
    print("  QuantEdge AI — Phase R: Strict 2026 Walk-Forward AI Training & Evaluation")
    print("=" * 80)
    print()

    if not master_csv.exists():
        print(f"[ERROR] 2026 SMC Master Dataset missing at {master_csv}!")
        print("Please run: python -m quantedge.ai.evaluation.extract_2026_smc_master_dataset")
        return 1

    print(f"[Phase R] Loading 2026 SMC Master Dataset from: {master_csv}")
    master_df = pd.read_csv(master_csv)
    print(f"[Phase R] Loaded {len(master_df)} qualified 2026 Order Blocks.")

    print("[Phase R] Initializing Phase R Walk-Forward Pipeline (5 Expanding Windows)...")
    pipeline = PhaseRWalkForwardPipeline(master_df)

    print("[Phase R] Executing progressive training & forward test replay...")
    prediction_records, results = pipeline.run_walk_forward()

    pop = results["population_summary"]
    agg = results["aggregate_oos_performance"]
    smc = agg["smc_baseline"]
    ai = agg["ai_filtered"]

    print("\n" + "=" * 80)
    print("  PHASE R WALK-FORWARD RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total 2026 Universe:        {pop['total_2026_obs']} Order Blocks")
    print(f"Seed Period (Jan-Mar):      {pop['seed_population_jan_mar']} Order Blocks")
    print(f"Walk-Forward Test Setups:   {pop['walk_forward_oos_setups']} Order Blocks (Apr-Aug 2026)")
    print()
    print("Aggregate Walk-Forward Out-of-Sample Performance:")
    print(f"  SMC Baseline Expectancy:  {smc['expectancy_r']:+.4f}R (WR: {smc['win_rate_pct']:.1f}%, PF: {smc['profit_factor']:.2f}, N={smc['n']})")
    print(f"  AI Filtered Expectancy:   {ai['expectancy_r']:+.4f}R (WR: {ai['win_rate_pct']:.1f}%, PF: {ai['profit_factor']:.2f}, N={ai['n']})")
    print(f"  Incremental Expectancy:   {agg['incremental_expectancy_r']:+.4f}R (Coverage: {ai['coverage_pct']:.1f}%)")
    print(f"  MBB 95% Confidence Int:   [{agg['bootstrap_95ci']['lower_95ci']:+.4f}R, {agg['bootstrap_95ci']['upper_95ci']:+.4f}R]")
    print(f"  Probability Delta > 0:    {agg['bootstrap_95ci']['p_value_greater_than_zero']*100:.1f}%")
    print()

    print("Per-Window Breakdown:")
    for w in results["window_results"]:
        smc_e = w["smc_baseline"]["expectancy_r"]
        ai_e = w["ai_filtered"]["expectancy_r"]
        print(f"  {w['window_id']} ({w['test_month']}): Train N={w['training_rows']} -> Test N={w['test_rows']} | Acc={w['accepted_count']} ({w['acceptance_rate_pct']:.1f}%) | SMC={smc_e:+.4f}R -> AI={ai_e:+.4f}R (d={w['incremental_expectancy_r']:+.4f}R)")

    print()
    print("[Phase R] Writing artifacts and reports...")
    artifacts = write_phase_r_artifacts(prediction_records, results, repo_root=repo_root)
    for name, path in artifacts.items():
        print(f"  Written {name}: {path}")

    print("\n" + "=" * 80)
    print("  PHASE R EVALUATION COMPLETE & DETERMINISTIC")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
