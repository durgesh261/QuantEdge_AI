"""
SOL-Only Execution & Analysis: Fixed +0.60% TP LuxAlgo Retest Engine.
Runs standalone backtest for SOLUSD from June 2024 to August 26, 2026,
and August 1-26, 2026 focused slice, exporting full trades CSV and statistics.
"""

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import json
import csv
from dataclasses import asdict

from quantedge.ai.evaluation.phase_l_research import _find_repo_root
from quantedge.ai.research.fixed_06_tp_luxalgo_retest_engine import (
    LuxAlgoRetestConfig,
    run_luxalgo_retest_backtest,
)

root = _find_repo_root()
docs_ai_dir = root / "docs" / "ai"
docs_ai_dir.mkdir(parents=True, exist_ok=True)
data_dir = root / "data" / "canonical" / "delta_exchange_india"

cfg = LuxAlgoRetestConfig(
    fixed_tp_market_pct=0.60,
    max_sl_account_risk_pct=35.0,
    applied_leverage_cap=100.0,
    penetration_depth=0.25,
    fee_rate=0.0008,
    starting_capital=10.0,
)

print("1. Running SOLUSD Standalone Multi-Year Backtest (2024-2026)...")
res_sol = run_luxalgo_retest_backtest(
    data_base_dir=data_dir, config=cfg, symbols=["SOLUSD"]
)

aug_start = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
print("2. Running SOLUSD August 1-26, 2026 Slice...")
aug_res_sol = run_luxalgo_retest_backtest(
    data_base_dir=data_dir, config=cfg, symbols=["SOLUSD"], start_date=aug_start
)

trades = res_sol["trades"]
trades_df = res_sol["trades_df"]

# -------------------------------------------------------------
# 1. Export Complete SOL Trades Ledger CSV
# -------------------------------------------------------------
sol_trades_csv_path = docs_ai_dir / "fixed_06_tp_sol_only_trades.csv"
fieldnames = [
    "trade_id", "asset", "direction", "ob_formation_time", "bos_time",
    "entry_time", "exit_time", "ob_formation_time_ist", "bos_time_ist",
    "entry_time_ist", "exit_time_ist", "ob_high", "ob_low", "ob_width",
    "ob_width_pct", "entry_price", "sl_price", "tp_price", "sl_distance_pct",
    "theoretical_leverage", "applied_leverage", "planned_tp_market_pct",
    "planned_sl_account_pct", "planned_tp_account_pct", "starting_capital",
    "position_notional", "gross_pnl", "fees", "net_pnl", "ending_capital",
    "outcome", "exit_reason", "is_ambiguous", "holding_bars", "holding_time_hours",
    "bars_ob_to_entry", "hours_ob_to_entry", "bars_retest_to_entry",
    "hours_retest_to_entry", "realized_r", "cumulative_realized_r",
    "data_timeframe", "trade_narrative"
]

with open(sol_trades_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for t in trades:
        d = asdict(t)
        d["starting_capital"] = f"{d['starting_capital']:.6f}" if d['starting_capital'] < 1e6 else f"{d['starting_capital']:.6e}"
        d["position_notional"] = f"{d['position_notional']:.6f}" if d['position_notional'] < 1e6 else f"{d['position_notional']:.6e}"
        d["gross_pnl"] = f"{d['gross_pnl']:.6f}" if abs(d['gross_pnl']) < 1e6 else f"{d['gross_pnl']:.6e}"
        d["fees"] = f"{d['fees']:.6f}" if d['fees'] < 1e6 else f"{d['fees']:.6e}"
        d["net_pnl"] = f"{d['net_pnl']:.6f}" if abs(d['net_pnl']) < 1e6 else f"{d['net_pnl']:.6e}"
        d["ending_capital"] = f"{d['ending_capital']:.6f}" if d['ending_capital'] < 1e6 else f"{d['ending_capital']:.6e}"
        writer.writerow(d)
print(f"Exported: {sol_trades_csv_path} ({len(trades)} trades)")

# -------------------------------------------------------------
# 2. Export Monthly Progression CSV for SOL
# -------------------------------------------------------------
sol_monthly_path = docs_ai_dir / "fixed_06_tp_sol_only_monthly.csv"
trades_df["month"] = pd.to_datetime(trades_df["entry_time"]).dt.to_period("M").astype(str)
monthly_records = []
for m, mdf in trades_df.groupby("month"):
    mw = mdf[mdf["outcome"] == "FILLED_TP"]
    ml = mdf[mdf["outcome"] == "FILLED_SL"]
    m_amb = mdf[mdf["is_ambiguous"] == True]
    m_n = len(mdf)
    m_wr = (len(mw) / m_n * 100.0) if m_n > 0 else 0.0
    m_r = float(mdf["realized_r"].sum())
    m_gain_r = float(mw["realized_r"].sum()) if len(mw) > 0 else 0.0
    m_loss_r = abs(float(ml["realized_r"].sum())) if len(ml) > 0 else 1.0
    m_pf = (m_gain_r / m_loss_r) if m_loss_r > 0 else 99.0
    m_start_cap = mdf.iloc[0]["starting_capital"]
    m_end_cap = mdf.iloc[-1]["ending_capital"]
    
    monthly_records.append({
        "month": m,
        "trades": m_n,
        "wins": len(mw),
        "losses": len(ml),
        "ambiguous": len(m_amb),
        "win_rate_pct": round(m_wr, 2),
        "realized_r": round(m_r, 2),
        "profit_factor": round(m_pf, 2),
        "starting_capital": f"{m_start_cap:.4f}" if m_start_cap < 1e6 else f"{m_start_cap:.4e}",
        "ending_capital": f"{m_end_cap:.4f}" if m_end_cap < 1e6 else f"{m_end_cap:.4e}",
    })
pd.DataFrame(monthly_records).to_csv(sol_monthly_path, index=False)
print(f"Exported: {sol_monthly_path}")

# -------------------------------------------------------------
# 3. Export SOL Results JSON
# -------------------------------------------------------------
sol_json_path = docs_ai_dir / "fixed_06_tp_sol_only_results.json"
json_data = {
    "experiment": "SOL-Only Fixed +0.60% TP LuxAlgo Retest Research",
    "period": "2024-06-11T00:00:00Z to 2026-08-26T14:00:00Z",
    "config": res_sol["config"],
    "summary": {
        "starting_capital": res_sol["starting_capital"],
        "ending_capital": res_sol["ending_capital_net"],
        "total_return_pct": res_sol["total_return_pct"],
        "total_obs_detected": res_sol["total_obs_detected"],
        "total_candidate_setups": res_sol["total_candidate_setups"],
        "total_executed_trades": res_sol["total_executed_trades"],
        "total_no_fill_setups": res_sol["total_no_fill_setups"],
        "total_touches_without_25pct": res_sol["total_touches_without_25pct"],
        "total_invalidations_before_fill": res_sol["total_invalidations_before_fill"],
        "total_skipped_global_lock": res_sol["total_skipped_global_lock"],
        "wins": res_sol["wins"],
        "losses": res_sol["losses"],
        "timeouts": res_sol["timeouts"],
        "ambiguous_trades": res_sol["ambiguous_trades"],
        "win_rate_pct": res_sol["win_rate_pct"],
        "expectancy_r": res_sol["expectancy_r"],
        "total_realized_r": res_sol["total_realized_r"],
        "profit_factor": res_sol["profit_factor"],
        "max_drawdown_pct": res_sol["max_drawdown_pct"],
        "max_losing_streak": res_sol["max_losing_streak"],
    },
    "holding_time_stats": res_sol["holding_time_stats"],
    "latency_stats": res_sol["latency_stats"],
    "latency_breakdown": res_sol["latency_breakdown"],
    "august_2026_slice": {
        "trades": aug_res_sol["total_executed_trades"],
        "wins": aug_res_sol["wins"],
        "losses": aug_res_sol["losses"],
        "win_rate_pct": aug_res_sol["win_rate_pct"],
        "total_realized_r": aug_res_sol["total_realized_r"],
        "profit_factor": aug_res_sol["profit_factor"],
    }
}
with open(sol_json_path, "w", encoding="utf-8") as f:
    json.dump(json_data, f, indent=2)
print(f"Exported: {sol_json_path}")

print("\n" + "=" * 80)
print("SOL-ONLY LUXALGO RETEST RESEARCH SUMMARY (2024-2026)")
print("=" * 80)
print(f"Total Candidate Setups:     {res_sol['total_candidate_setups']}")
print(f"Executed Trades:            {res_sol['total_executed_trades']}")
print(f"Wins / Losses:              {res_sol['wins']} W / {res_sol['losses']} L")
print(f"Win Rate %:                 {res_sol['win_rate_pct']}%")
print(f"Total Realized R:           {res_sol['total_realized_r']}R")
print(f"Profit Factor:              {res_sol['profit_factor']}")
print(f"Max Losing Streak:          {res_sol['max_losing_streak']} trades")
print(f"August 2026 (Aug 1-26):     {aug_res_sol['total_executed_trades']} trades | {aug_res_sol['wins']} W / {aug_res_sol['losses']} L ({aug_res_sol['win_rate_pct']}%) | Realized R: {aug_res_sol['total_realized_r']}R")
print("=" * 80)

print("\nRETEST LATENCY PERFORMANCE ON SOL-ONLY:")
for k, v in res_sol["latency_breakdown"].items():
    print(f"{k:<20} | Trades: {v['trades']:<4} | Wins: {v['wins']:<4} | Losses: {v['losses']:<4} | WR: {v['win_rate_pct']:<6.2f}% | R: {v['total_realized_r']:<7.2f}R | PF: {v['profit_factor']}")
print("=" * 80)
