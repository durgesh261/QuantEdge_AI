"""
Full Experiment Runner: Fixed +0.60% TP LuxAlgo Retest Research.
Generates all required CSVs, JSON, and comprehensive markdown report.
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

print("1. Running Full Multi-Year LuxAlgo Retest Backtest (2024-2026)...")
res = run_luxalgo_retest_backtest(data_base_dir=data_dir, config=cfg)

# August 1 to August 26, 2026 Focused Slice
aug_start = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
print("2. Running August 1-26, 2026 Slice...")
aug_res = run_luxalgo_retest_backtest(
    data_base_dir=data_dir, config=cfg, start_date=aug_start
)

trades = res["trades"]
trades_df = res["trades_df"]

# -------------------------------------------------------------
# 1. Export Complete Trades Ledger CSV
# -------------------------------------------------------------
trades_csv_path = docs_ai_dir / "fixed_06_tp_luxalgo_retest_trades.csv"
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

with open(trades_csv_path, "w", newline="", encoding="utf-8") as f:
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
print(f"Exported: {trades_csv_path} ({len(trades)} trades)")

# -------------------------------------------------------------
# 2. Export Monthly Progression CSV
# -------------------------------------------------------------
monthly_path = docs_ai_dir / "fixed_06_tp_luxalgo_retest_monthly.csv"
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
pd.DataFrame(monthly_records).to_csv(monthly_path, index=False)
print(f"Exported: {monthly_path}")

# -------------------------------------------------------------
# 3. Export Asset Breakdown CSV
# -------------------------------------------------------------
asset_path = docs_ai_dir / "fixed_06_tp_luxalgo_retest_asset_breakdown.csv"
pd.DataFrame(list(res["asset_breakdown"].values())).to_csv(asset_path, index=False)
print(f"Exported: {asset_path}")

# -------------------------------------------------------------
# 4. Export Results JSON
# -------------------------------------------------------------
json_path = docs_ai_dir / "fixed_06_tp_luxalgo_retest_results.json"
json_data = {
    "experiment": "Fixed +0.60% TP LuxAlgo Retest Research",
    "period": "2024-06-01T00:00:00Z to 2026-08-26T14:00:00Z",
    "config": res["config"],
    "summary": {
        "starting_capital": res["starting_capital"],
        "ending_capital": res["ending_capital_net"],
        "total_return_pct": res["total_return_pct"],
        "total_obs_detected": res["total_obs_detected"],
        "total_candidate_setups": res["total_candidate_setups"],
        "total_executed_trades": res["total_executed_trades"],
        "total_no_fill_setups": res["total_no_fill_setups"],
        "total_touches_without_25pct": res["total_touches_without_25pct"],
        "total_invalidations_before_fill": res["total_invalidations_before_fill"],
        "total_skipped_global_lock": res["total_skipped_global_lock"],
        "wins": res["wins"],
        "losses": res["losses"],
        "timeouts": res["timeouts"],
        "ambiguous_trades": res["ambiguous_trades"],
        "win_rate_pct": res["win_rate_pct"],
        "expectancy_r": res["expectancy_r"],
        "total_realized_r": res["total_realized_r"],
        "profit_factor": res["profit_factor"],
        "max_drawdown_pct": res["max_drawdown_pct"],
        "max_losing_streak": res["max_losing_streak"],
    },
    "holding_time_stats": res["holding_time_stats"],
    "latency_stats": res["latency_stats"],
    "asset_breakdown": res["asset_breakdown"],
    "latency_breakdown": res["latency_breakdown"],
    "august_2026_slice": {
        "trades": aug_res["total_executed_trades"],
        "wins": aug_res["wins"],
        "losses": aug_res["losses"],
        "win_rate_pct": aug_res["win_rate_pct"],
        "total_realized_r": aug_res["total_realized_r"],
        "profit_factor": aug_res["profit_factor"],
    }
}
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(json_data, f, indent=2)
print(f"Exported: {json_path}")

print("\n" + "=" * 80)
print("LUXALGO RETEST RESEARCH SUMMARY (2024-2026)")
print("=" * 80)
print(f"Total Candidate Setups:     {res['total_candidate_setups']}")
print(f"Executed Trades:            {res['total_executed_trades']}")
print(f"Invalidated Before Fill:    {res['total_invalidations_before_fill']}")
print(f"Touches Without 25% Fill:   {res['total_touches_without_25pct']}")
print(f"Wins / Losses:              {res['wins']} W / {res['losses']} L")
print(f"Win Rate %:                 {res['win_rate_pct']}%")
print(f"Total Realized R:           {res['total_realized_r']}R")
print(f"Profit Factor:              {res['profit_factor']}")
print(f"Max Drawdown %:             {res['max_drawdown_pct']}%")
print(f"Max Losing Streak:          {res['max_losing_streak']} trades")
print(f"August 2026 (Aug 1-26):     {aug_res['total_executed_trades']} trades | {aug_res['wins']} W / {aug_res['losses']} L ({aug_res['win_rate_pct']}%) | Realized R: {aug_res['total_realized_r']}R")
print("=" * 80)
