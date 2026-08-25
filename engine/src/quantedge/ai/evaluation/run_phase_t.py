"""
QuantEdge AI — Phase T Multi-Year Expanding Walk-Forward Evaluation Runner CLI.

Usage:
    python -m quantedge.ai.evaluation.run_phase_t
"""

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

from quantedge.ai.evaluation.extract_multiyear_smc_master_dataset import (
    extract_multiyear_smc_master_dataset,
    write_multiyear_master_artifacts,
)
from quantedge.ai.evaluation.phase_t_multiyear import (
    PhaseTMultiYearWalkForwardPipeline,
    _find_repo_root,
    write_phase_t_artifacts,
)


def main() -> int:
    repo_root = _find_repo_root()
    master_csv = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"

    print("=" * 80)
    print("  QuantEdge AI — Phase T: Multi-Year Expanding Walk-Forward Evaluation")
    print("=" * 80)
    print()

    if not master_csv.exists():
        print(f"[Phase T] Master dataset missing at {master_csv}. Extracting from canonical data...")
        df_master, meta = extract_multiyear_smc_master_dataset(repo_root=repo_root)
        artifacts = write_multiyear_master_artifacts(df_master, meta, repo_root=repo_root)
        print(f"[Phase T] Multi-year master dataset created ({len(df_master)} OBs).")
    else:
        print(f"[Phase T] Loading existing Multi-Year Master Dataset from: {master_csv}")
        df_master = pd.read_csv(master_csv)
        print(f"[Phase T] Loaded {len(df_master)} qualified Order Blocks.")

    print("\n[Phase T] Initializing 20-Month Expanding Walk-Forward Pipeline...")
    pipeline = PhaseTMultiYearWalkForwardPipeline(df_master)

    print("[Phase T] Executing 20 monthly expanding windows (Jan 2025 – Aug 2026)...")
    records, results = pipeline.run_multiyear_evaluation()

    rep = results["aggregate_oos_performance"]
    boot = results["moving_block_bootstrap"]
    r_bm = results["random_coverage_benchmark"]
    econ = results["economic_analysis"]
    cls = results["evidence_classification"]

    print("\n" + "=" * 80)
    print("  PHASE T EVALUATION RESULTS (20-MONTH OOS: JAN 2025 – AUG 2026)")
    print("=" * 80)
    print(f"Overall Classification:        {cls['classification']}")
    print(f"Summary:                       {cls['summary']}")
    print()
    print("Aggregate Out-of-Sample Performance:")
    print(f"  Total OOS Setups:            {rep['total_oos_setups']}")
    print(f"  AI Accepted Setups:          {rep['ai_accepted_count']} ({rep['ai_filtered']['coverage_pct']:.2f}% coverage)")
    print(f"  SMC Baseline Expectancy:     {rep['smc_baseline']['expectancy_r']:+.4f}R (WR: {rep['smc_baseline']['win_rate_pct']:.2f}%, PF: {rep['smc_baseline']['profit_factor']:.2f}, Tot: {rep['smc_baseline']['total_r']:+.2f}R, MDD: {rep['smc_baseline']['max_drawdown_r']:.2f}R)")
    print(f"  AI Filtered Expectancy:      {rep['ai_filtered']['expectancy_r']:+.4f}R (WR: {rep['ai_filtered']['win_rate_pct']:.2f}%, PF: {rep['ai_filtered']['profit_factor']:.2f}, Tot: {rep['ai_filtered']['total_r']:+.2f}R, MDD: {rep['ai_filtered']['max_drawdown_r']:.2f}R)")
    print(f"  Incremental Expectancy (Delta): {rep['incremental_expectancy_r']:+.4f}R")
    print(f"  Max Drawdown Reduction:      {rep['ai_filtered']['max_drawdown_r'] - rep['smc_baseline']['max_drawdown_r']:+.2f}R ({(1 - rep['ai_filtered']['max_drawdown_r']/rep['smc_baseline']['max_drawdown_r'])*100:.1f}% risk reduction)")
    print()
    print("Moving Block Bootstrap (10,000 resamples, b=36):")
    print(f"  SMC Expectancy 95% CI:       [{boot['smc_expectancy_95ci'][0]:+.4f}R, {boot['smc_expectancy_95ci'][1]:+.4f}R]")
    print(f"  AI Expectancy 95% CI:        [{boot['ai_expectancy_95ci'][0]:+.4f}R, {boot['ai_expectancy_95ci'][1]:+.4f}R]")
    print(f"  Incremental Delta 95% CI:    [{boot['incremental_expectancy_95ci'][0]:+.4f}R, {boot['incremental_expectancy_95ci'][1]:+.4f}R]")
    print(f"  Probability Delta > 0:       {boot['p_value_incremental_greater_than_zero']*100:.1f}%")
    print()
    print("Coverage-Matched Random Benchmark (10,000 resamples):")
    print(f"  Ridge Realized Expectancy:   {r_bm['ridge_realized_expectancy']:+.4f}R")
    print(f"  Random Mean Expectancy:      {r_bm['random_benchmark_mean_exp']:+.4f}R (95% CI: [{r_bm['random_benchmark_95ci'][0]:+.4f}R, {r_bm['random_benchmark_95ci'][1]:+.4f}R])")
    print(f"  Ridge Percentile in Random:  {r_bm['ridge_percentile_rank_in_random_distribution']:.1f}th Percentile (p = {r_bm['empirical_p_value_ridge_vs_random']:.4f})")
    print()
    print("Conservative 1.0% Fixed-Fractional Growth ($10,000 Base):")
    print(f"  SMC Baseline Return:         {econ['smc_net_return_pct']:+.2f}% (Terminal: ${econ['smc_terminal_balance_usd']:,.2f}, MDD: {econ['smc_max_drawdown_pct']:.2f}%)")
    print(f"  AI Filtered Return:          {econ['ai_net_return_pct']:+.2f}% (Terminal: ${econ['ai_terminal_balance_usd']:,.2f}, MDD: {econ['ai_max_drawdown_pct']:.2f}%)")
    print()

    print("[Phase T] Writing artifacts and report...")
    artifacts = write_phase_t_artifacts(records, results, repo_root=repo_root)
    for name, path in artifacts.items():
        print(f"  Written {name}: {path}")

    print("\n" + "=" * 80)
    print("  PHASE T EVALUATION COMPLETE & DETERMINISTIC")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
