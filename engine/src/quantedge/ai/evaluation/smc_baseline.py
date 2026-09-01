"""
Deterministic SMC Strategy Performance Baseline Evaluator.

Evaluates trade setup outcomes from the deterministic SMC engine without any ML/AI filtering.
Calculates standardized financial metrics (Win Rate, Profit Factor, Expectancy, Max Drawdown in R, MFE/MAE).
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from quantedge.ai.training.real_dataset_builder import (
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
)


@dataclass(frozen=True)
class PerformanceMetrics:
    """Standardized trading performance metrics."""
    total_setups: int
    executed_setups: int
    coverage_pct: float
    win_count: int
    loss_count: int
    timeout_count: int
    win_rate_pct: float
    loss_rate_pct: float
    timeout_rate_pct: float
    mean_r: float
    median_r: float
    total_r: float
    profit_factor: float
    expectancy_r: float
    max_drawdown_r: float
    mean_mfe_r: float
    mean_mae_r: float
    avg_holding_bars: float
    high_conf_win_rate_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_performance_metrics(
    df: pd.DataFrame,
    r_col: str = TARGET_REALIZED_R,
    mfe_col: str = TARGET_MFE_R,
    mae_col: str = TARGET_MAE_R,
    total_eligible_setups: Optional[int] = None,
) -> PerformanceMetrics:
    """
    Computes rigorous trading statistics from a DataFrame of trades.

    Args:
        df: DataFrame containing trade outcome columns.
        r_col: Column name for realized R.
        mfe_col: Column name for MFE in R units.
        mae_col: Column name for MAE in R units.
        total_eligible_setups: Denominator for coverage calculation (defaults to len(df)).

    Returns:
        PerformanceMetrics dataclass.
    """
    n_exec = len(df)
    n_total = total_eligible_setups if total_eligible_setups is not None else n_exec
    coverage = (n_exec / n_total * 100.0) if n_total > 0 else 0.0

    if n_exec == 0:
        return PerformanceMetrics(
            total_setups=n_total,
            executed_setups=0,
            coverage_pct=0.0,
            win_count=0,
            loss_count=0,
            timeout_count=0,
            win_rate_pct=0.0,
            loss_rate_pct=0.0,
            timeout_rate_pct=0.0,
            mean_r=0.0,
            median_r=0.0,
            total_r=0.0,
            profit_factor=0.0,
            expectancy_r=0.0,
            max_drawdown_r=0.0,
            mean_mfe_r=0.0,
            mean_mae_r=0.0,
            avg_holding_bars=0.0,
            high_conf_win_rate_pct=None,
        )

    r_vals = df[r_col].to_numpy(dtype=float)
    wins = np.sum(r_vals > 0.0)
    losses = np.sum(r_vals < 0.0)
    timeouts = np.sum(r_vals == 0.0)

    # Check exit reason column if present for exact categorization
    if "exit_reason" in df.columns:
        exit_reasons = df["exit_reason"].astype(str).values
        wins = int(np.sum(exit_reasons == "TP_HIT") + np.sum((exit_reasons == "TIMEOUT_EXIT") & (r_vals > 0)))
        losses = int(np.sum(exit_reasons == "SL_HIT") + np.sum((exit_reasons == "TIMEOUT_EXIT") & (r_vals < 0)))
        timeouts = int(np.sum((exit_reasons == "TIMEOUT_EXIT") & (r_vals == 0)))
    else:
        wins = int(wins)
        losses = int(losses)
        timeouts = int(timeouts)

    win_rate = (wins / n_exec) * 100.0
    loss_rate = (losses / n_exec) * 100.0
    timeout_rate = (timeouts / n_exec) * 100.0

    mean_r = float(np.mean(r_vals))
    median_r = float(np.median(r_vals))
    total_r = float(np.sum(r_vals))

    # Profit Factor: Gross Profits / Gross Losses
    gross_profits = float(np.sum(r_vals[r_vals > 0.0]))
    gross_losses = float(np.abs(np.sum(r_vals[r_vals < 0.0])))
    if gross_losses > 1e-6:
        profit_factor = gross_profits / gross_losses
    elif gross_profits > 0:
        profit_factor = 999.0  # Infinite profit factor if no losses
    else:
        profit_factor = 0.0

    expectancy = mean_r  # Expectancy per trade in R units

    # Cumulative R and Max Drawdown in R units
    cum_r = np.cumsum(r_vals)
    cum_max = np.maximum.accumulate(cum_r)
    drawdowns = cum_max - cum_r
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Excursion metrics
    mfe_vals = df[mfe_col].to_numpy(dtype=float) if mfe_col in df.columns else np.zeros(n_exec)
    mae_vals = df[mae_col].to_numpy(dtype=float) if mae_col in df.columns else np.zeros(n_exec)
    mean_mfe = float(np.mean(mfe_vals))
    mean_mae = float(np.mean(mae_vals))

    # Holding bars
    if "holding_bars" in df.columns:
        avg_holding = float(np.mean(df["holding_bars"].to_numpy(dtype=float)))
    else:
        avg_holding = 0.0

    return PerformanceMetrics(
        total_setups=n_total,
        executed_setups=n_exec,
        coverage_pct=round(coverage, 1),
        win_count=wins,
        loss_count=losses,
        timeout_count=timeouts,
        win_rate_pct=round(win_rate, 2),
        loss_rate_pct=round(loss_rate, 2),
        timeout_rate_pct=round(timeout_rate, 2),
        mean_r=round(mean_r, 4),
        median_r=round(median_r, 4),
        total_r=round(total_r, 2),
        profit_factor=round(profit_factor, 3),
        expectancy_r=round(expectancy, 4),
        max_drawdown_r=round(max_dd, 2),
        mean_mfe_r=round(mean_mfe, 3),
        mean_mae_r=round(mean_mae, 3),
        avg_holding_bars=round(avg_holding, 1),
    )


def format_performance_table(smc_perf: PerformanceMetrics, ai_perf: PerformanceMetrics) -> str:
    """Formats markdown comparison table between SMC Only and SMC + AI."""
    lines = [
        "| Metric | SMC Only | SMC + AI | Change / Impact |",
        "|---|---:|---:|---:|",
        f"| **Total Setups** | {smc_perf.total_setups} | {ai_perf.total_setups} | — |",
        f"| **Executed / Eligible Setups** | {smc_perf.executed_setups} | {ai_perf.executed_setups} | `{ai_perf.coverage_pct:.1f}% coverage` |",
        f"| **Win Rate** | {smc_perf.win_rate_pct:.1f}% ({smc_perf.win_count}) | {ai_perf.win_rate_pct:.1f}% ({ai_perf.win_count}) | `{ai_perf.win_rate_pct - smc_perf.win_rate_pct:+.1f}%` |",
        f"| **Loss Rate** | {smc_perf.loss_rate_pct:.1f}% ({smc_perf.loss_count}) | {ai_perf.loss_rate_pct:.1f}% ({ai_perf.loss_count}) | `{ai_perf.loss_rate_pct - smc_perf.loss_rate_pct:+.1f}%` |",
        f"| **Timeout Rate** | {smc_perf.timeout_rate_pct:.1f}% ({smc_perf.timeout_count}) | {ai_perf.timeout_rate_pct:.1f}% ({ai_perf.timeout_count}) | `{ai_perf.timeout_rate_pct - smc_perf.timeout_rate_pct:+.1f}%` |",
        f"| **Mean R** | {smc_perf.mean_r:+.4f}R | {ai_perf.mean_r:+.4f}R | `{ai_perf.mean_r - smc_perf.mean_r:+.4f}R` |",
        f"| **Median R** | {smc_perf.median_r:+.4f}R | {ai_perf.median_r:+.4f}R | `{ai_perf.median_r - smc_perf.median_r:+.4f}R` |",
        f"| **Total Realized R** | {smc_perf.total_r:+.2f}R | {ai_perf.total_r:+.2f}R | `{ai_perf.total_r - smc_perf.total_r:+.2f}R` |",
        f"| **Profit Factor** | {smc_perf.profit_factor:.3f} | {ai_perf.profit_factor:.3f} | `{ai_perf.profit_factor - smc_perf.profit_factor:+.3f}` |",
        f"| **Expectancy** | {smc_perf.expectancy_r:+.4f}R | {ai_perf.expectancy_r:+.4f}R | `{ai_perf.expectancy_r - smc_perf.expectancy_r:+.4f}R` |",
        f"| **Max Drawdown** | {smc_perf.max_drawdown_r:.2f}R | {ai_perf.max_drawdown_r:.2f}R | `{ai_perf.max_drawdown_r - smc_perf.max_drawdown_r:+.2f}R` |",
        f"| **Mean MFE** | {smc_perf.mean_mfe_r:.3f}R | {ai_perf.mean_mfe_r:.3f}R | `{ai_perf.mean_mfe_r - smc_perf.mean_mfe_r:+.3f}R` |",
        f"| **Mean MAE** | {smc_perf.mean_mae_r:.3f}R | {ai_perf.mean_mae_r:.3f}R | `{ai_perf.mean_mae_r - smc_perf.mean_mae_r:+.3f}R` |",
        f"| **Avg Holding Time** | {smc_perf.avg_holding_bars:.1f} bars | {ai_perf.avg_holding_bars:.1f} bars | `{ai_perf.avg_holding_bars - smc_perf.avg_holding_bars:+.1f} bars` |",
    ]
    return "\n".join(lines)
