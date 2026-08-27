"""
Comprehensive Runner & Analysis: First-Touch 3-Candle Qualification / OB Expiry.
Generates all 5 CSVs, Results JSON, and prints side-by-side comparison tables.
"""

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import json
import csv
from dataclasses import asdict

from quantedge.ai.evaluation.phase_l_research import _find_repo_root
from quantedge.ai.research.first_touch_3_candle_engine import (
    FirstTouchConfig,
    run_first_touch_3_candle_backtest,
)

root = _find_repo_root()
docs_ai_dir = root / "docs" / "ai"
docs_ai_dir.mkdir(parents=True, exist_ok=True)
data_dir = root / "data" / "canonical" / "delta_exchange_india"

cfg = FirstTouchConfig(
    fixed_tp_pct=0.60,
    max_sl_risk_pct=35.0,
    max_leverage=100.0,
    penetration_depth=0.25,
    qualification_window_bars=3,
    starting_capital=10.0,
)

print("1. Running Full Multi-Year Baseline (2024-2026)...")
baseline_res = run_first_touch_3_candle_backtest(
    data_base_dir=data_dir, config=cfg, enforce_3_candle_rule=False
)

print("2. Running Full Multi-Year New Strategy (First-Touch 3-Candle Expiry)...")
new_res = run_first_touch_3_candle_backtest(
    data_base_dir=data_dir, config=cfg, enforce_3_candle_rule=True
)

# August 1 to August 26, 2026 Focused Slice
aug_start = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
print("3. Running August 2026 Baseline...")
aug_base_res = run_first_touch_3_candle_backtest(
    data_base_dir=data_dir, config=cfg, enforce_3_candle_rule=False, start_date=aug_start
)
print("4. Running August 2026 New Strategy...")
aug_new_res = run_first_touch_3_candle_backtest(
    data_base_dir=data_dir, config=cfg, enforce_3_candle_rule=True, start_date=aug_start
)

# Standalone Per-Asset runs under New Strategy
standalone_results = {}
for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
    print(f"Running Standalone for {sym}...")
    s_base = run_first_touch_3_candle_backtest(
        data_base_dir=data_dir, config=cfg, symbols=[sym], enforce_3_candle_rule=False
    )
    s_new = run_first_touch_3_candle_backtest(
        data_base_dir=data_dir, config=cfg, symbols=[sym], enforce_3_candle_rule=True
    )
    standalone_results[sym] = {"baseline": s_base, "new": s_new}

# -------------------------------------------------------------
# Removed Trades Attribution Analysis
# -------------------------------------------------------------
base_trades = baseline_res["trades"]
new_trades = new_res["trades"]

base_df = baseline_res["trades_df"]
new_df = new_res["trades_df"]

# Find baseline setups that executed in baseline but whose OBs were expired by 3-candle rule
base_ob_set = set(t.setup_time + "_" + t.asset for t in base_trades)
new_ob_set = set(t.setup_time + "_" + t.asset for t in new_trades)

removed_records = []
for t in base_trades:
    key = t.setup_time + "_" + t.asset
    if key not in new_ob_set:
        impact_type = "SAVED_LOSS" if t.outcome == "FILLED_SL" else ("MISSED_WIN" if t.outcome == "FILLED_TP" else "NEUTRAL")
        removed_records.append({
            "asset": t.asset,
            "direction": t.direction,
            "setup_time": t.setup_time,
            "setup_time_ist": t.setup_time_ist,
            "first_touch_time": t.first_touch_time,
            "first_touch_time_ist": t.first_touch_time_ist,
            "bars_from_touch_to_old_entry": t.touch_to_fill_bars,
            "old_entry_time": t.entry_time,
            "old_entry_time_ist": t.entry_time_ist,
            "old_exit_time": t.exit_time,
            "old_exit_time_ist": t.exit_time_ist,
            "entry_price": t.entry_price,
            "sl_price": t.sl_price,
            "tp_price": t.tp_price,
            "leverage": t.leverage,
            "old_outcome": t.outcome,
            "old_realized_r": t.realized_r,
            "old_net_pnl": t.net_pnl,
            "removal_reason": "Failed to reach 25% entry within 3 candles of first touch",
            "capital_impact_type": impact_type,
        })

# -------------------------------------------------------------
# Export Artifacts
# -------------------------------------------------------------

# 1. New Trades CSV
new_trades_path = docs_ai_dir / "first_touch_3_candle_trades.csv"
fieldnames = [
    "trade_id", "asset", "direction", "setup_time", "setup_time_ist",
    "first_touch_time", "first_touch_time_ist", "entry_time", "entry_time_ist",
    "exit_time", "exit_time_ist", "touch_to_fill_bars",
    "ob_high", "ob_low", "ob_width", "ob_width_pct", "entry_price",
    "sl_price", "tp_price", "sl_distance_pct", "leverage",
    "actual_sl_risk_pct", "tp_target_return_pct",
    "starting_capital", "position_notional", "gross_pnl", "fees",
    "net_pnl", "ending_capital", "outcome", "exit_reason",
    "is_ambiguous", "holding_bars", "holding_time_hours",
    "realized_r", "cumulative_realized_r", "data_timeframe", "trade_narrative"
]
with open(new_trades_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for t in new_trades:
        d = asdict(t)
        d["starting_capital"] = f"{d['starting_capital']:.6f}" if d['starting_capital'] < 1e6 else f"{d['starting_capital']:.6e}"
        d["position_notional"] = f"{d['position_notional']:.6f}" if d['position_notional'] < 1e6 else f"{d['position_notional']:.6e}"
        d["gross_pnl"] = f"{d['gross_pnl']:.6f}" if abs(d['gross_pnl']) < 1e6 else f"{d['gross_pnl']:.6e}"
        d["fees"] = f"{d['fees']:.6f}" if d['fees'] < 1e6 else f"{d['fees']:.6e}"
        d["net_pnl"] = f"{d['net_pnl']:.6f}" if abs(d['net_pnl']) < 1e6 else f"{d['net_pnl']:.6e}"
        d["ending_capital"] = f"{d['ending_capital']:.6f}" if d['ending_capital'] < 1e6 else f"{d['ending_capital']:.6e}"
        writer.writerow(d)
print(f"Exported: {new_trades_path}")

# 2. Monthly Progression CSV
new_df["month"] = pd.to_datetime(new_df["entry_time"]).dt.to_period("M").astype(str)
monthly_records = []
for m, mdf in new_df.groupby("month"):
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
monthly_path = docs_ai_dir / "first_touch_3_candle_monthly.csv"
pd.DataFrame(monthly_records).to_csv(monthly_path, index=False)
print(f"Exported: {monthly_path}")

# 3. Asset Breakdown CSV
asset_records = []
for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
    b_stat = baseline_res["asset_breakdown"][sym]
    n_stat = new_res["asset_breakdown"][sym]
    asset_records.append({
        "asset": sym,
        "baseline_setups": b_stat["total_setups"],
        "baseline_trades": b_stat["filled_trades"],
        "baseline_win_rate_pct": b_stat["win_rate_pct"],
        "baseline_realized_r": b_stat["total_realized_r"],
        "baseline_profit_factor": b_stat["profit_factor"],
        "new_trades": n_stat["filled_trades"],
        "new_win_rate_pct": n_stat["win_rate_pct"],
        "new_realized_r": n_stat["total_realized_r"],
        "new_profit_factor": n_stat["profit_factor"],
        "delta_win_rate_pct": round(n_stat["win_rate_pct"] - b_stat["win_rate_pct"], 2),
        "delta_realized_r": round(n_stat["total_realized_r"] - b_stat["total_realized_r"], 2),
    })
asset_path = docs_ai_dir / "first_touch_3_candle_asset_breakdown.csv"
pd.DataFrame(asset_records).to_csv(asset_path, index=False)
print(f"Exported: {asset_path}")

# 4. Removed Trades CSV
removed_path = docs_ai_dir / "first_touch_3_candle_removed_trades.csv"
pd.DataFrame(removed_records).to_csv(removed_path, index=False)
print(f"Exported: {removed_path} ({len(removed_records)} removed trades)")

# 5. Results JSON
json_data = {
    "experiment": "First-Touch 3-Candle Qualification / OB Expiry",
    "period": "2024-06-01T00:00:00Z to 2026-08-26T14:00:00Z",
    "baseline": {
        "executed_trades": baseline_res["total_executed_trades"],
        "wins": baseline_res["wins"],
        "losses": baseline_res["losses"],
        "win_rate_pct": baseline_res["win_rate_pct"],
        "total_realized_r": baseline_res["total_realized_r"],
        "expectancy_r": baseline_res["expectancy_r"],
        "profit_factor": baseline_res["profit_factor"],
        "max_drawdown_pct": baseline_res["max_drawdown_pct"],
        "max_losing_streak": baseline_res["max_losing_streak"],
        "ending_capital": baseline_res["ending_capital_net"],
    },
    "new_strategy": {
        "executed_trades": new_res["total_executed_trades"],
        "first_touch_expirations": new_res["total_first_touch_expirations"],
        "wins": new_res["wins"],
        "losses": new_res["losses"],
        "win_rate_pct": new_res["win_rate_pct"],
        "total_realized_r": new_res["total_realized_r"],
        "expectancy_r": new_res["expectancy_r"],
        "profit_factor": new_res["profit_factor"],
        "max_drawdown_pct": new_res["max_drawdown_pct"],
        "max_losing_streak": new_res["max_losing_streak"],
        "ending_capital": new_res["ending_capital_net"],
    },
    "delta": {
        "delta_executed_trades": new_res["total_executed_trades"] - baseline_res["total_executed_trades"],
        "delta_win_rate_pct": round(new_res["win_rate_pct"] - baseline_res["win_rate_pct"], 2),
        "delta_realized_r": round(new_res["total_realized_r"] - baseline_res["total_realized_r"], 2),
        "delta_profit_factor": round(new_res["profit_factor"] - baseline_res["profit_factor"], 2),
    },
    "august_2026_slice": {
        "baseline_trades": aug_base_res["total_executed_trades"],
        "baseline_win_rate_pct": aug_base_res["win_rate_pct"],
        "baseline_realized_r": aug_base_res["total_realized_r"],
        "new_trades": aug_new_res["total_executed_trades"],
        "new_win_rate_pct": aug_new_res["win_rate_pct"],
        "new_realized_r": aug_new_res["total_realized_r"],
    },
    "removed_trades_summary": {
        "total_removed": len(removed_records),
        "saved_losses": len([r for r in removed_records if r["capital_impact_type"] == "SAVED_LOSS"]),
        "missed_wins": len([r for r in removed_records if r["capital_impact_type"] == "MISSED_WIN"]),
    },
}
json_path = docs_ai_dir / "first_touch_3_candle_results.json"
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(json_data, f, indent=2)
print(f"Exported: {json_path}")

print("\n" + "=" * 80)
print("FINAL RESULTS SUMMARY: BASELINE vs NEW STRATEGY")
print("=" * 80)
print(f"Metric                    | Baseline (No Expiry) | New (3-Candle Expiry) | Delta")
print("-" * 80)
print(f"Executed Trades           | {baseline_res['total_executed_trades']:<20} | {new_res['total_executed_trades']:<21} | {new_res['total_executed_trades'] - baseline_res['total_executed_trades']:+d}")
print(f"First-Touch Expirations   | 0                    | {new_res['total_first_touch_expirations']:<21} | +{new_res['total_first_touch_expirations']}")
print(f"Wins / Losses             | {baseline_res['wins']} W / {baseline_res['losses']} L       | {new_res['wins']} W / {new_res['losses']} L        | {new_res['wins'] - baseline_res['wins']:+d} W / {new_res['losses'] - baseline_res['losses']:+d} L")
print(f"Win Rate %                | {baseline_res['win_rate_pct']:<20}% | {new_res['win_rate_pct']:<21}% | {new_res['win_rate_pct'] - baseline_res['win_rate_pct']:+.2f}%")
print(f"Total Realized R          | {baseline_res['total_realized_r']:<20}R | {new_res['total_realized_r']:<21}R | {new_res['total_realized_r'] - baseline_res['total_realized_r']:+.2f}R")
print(f"Profit Factor             | {baseline_res['profit_factor']:<20} | {new_res['profit_factor']:<21} | {new_res['profit_factor'] - baseline_res['profit_factor']:+.2f}")
print(f"Max Losing Streak         | {baseline_res['max_losing_streak']:<20} | {new_res['max_losing_streak']:<21} | {new_res['max_losing_streak'] - baseline_res['max_losing_streak']:+d}")
print("-" * 80)
print(f"Removed Trades Analysis: Total Removed = {len(removed_records)} (Saved Losses: {len([r for r in removed_records if r['capital_impact_type'] == 'SAVED_LOSS'])}, Missed Wins: {len([r for r in removed_records if r['capital_impact_type'] == 'MISSED_WIN'])})")
print("=" * 80)
