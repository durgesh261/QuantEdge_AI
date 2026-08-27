"""
Empirical Segmentation Study: Retest Latency & Order Block Characteristics.
Analyzes the 1,203 clean baseline trades across time-to-fill, touch-to-fill, zone width,
direction, asset, and holding dynamics to pinpoint the genuine source of edge.
"""

from pathlib import Path
import pandas as pd
import numpy as np

from quantedge.ai.evaluation.phase_l_research import _find_repo_root
from quantedge.ai.research.first_touch_3_candle_engine import (
    FirstTouchConfig,
    run_first_touch_3_candle_backtest,
)

root = _find_repo_root()
data_dir = root / "data" / "canonical" / "delta_exchange_india"

cfg = FirstTouchConfig(
    fixed_tp_pct=0.60,
    max_sl_risk_pct=35.0,
    max_leverage=100.0,
    penetration_depth=0.25,
    starting_capital=10.0,
)

print("Running baseline backtest to extract detailed trade attributes...")
res = run_first_touch_3_candle_backtest(
    data_base_dir=data_dir, config=cfg, enforce_3_candle_rule=False
)

trades_df = res["trades_df"]
print(f"Total Trades Analyzed: {len(trades_df)}")

# Create segmentation buckets
# 1. Total Bars from Setup to 25% Entry (Holding bars before fill)
# We can compute bars_from_setup_to_entry = (entry_time - setup_time) in hours
trades_df["dt_setup"] = pd.to_datetime(trades_df["setup_time"])
trades_df["dt_entry"] = pd.to_datetime(trades_df["entry_time"])
trades_df["dt_exit"] = pd.to_datetime(trades_df["exit_time"])
trades_df["dt_touch"] = pd.to_datetime(trades_df["first_touch_time"])

trades_df["hours_setup_to_entry"] = (trades_df["dt_entry"] - trades_df["dt_setup"]).dt.total_seconds() / 3600.0
trades_df["hours_touch_to_entry"] = (trades_df["dt_entry"] - trades_df["dt_touch"]).dt.total_seconds() / 3600.0
trades_df["is_win"] = (trades_df["outcome"] == "FILLED_TP").astype(int)

# Bins for hours_setup_to_entry
bins_setup = [0, 1, 2, 3, 6, 13, 25, 73]
labels_setup = ["1h (Immediate)", "2h", "3h", "4-6h", "7-12h", "13-24h", ">24h"]
trades_df["bin_setup_to_entry"] = pd.cut(trades_df["hours_setup_to_entry"], bins=bins_setup, labels=labels_setup, right=True)

# Bins for hours_touch_to_entry
bins_touch = [-1, 0.5, 2.5, 5.5, 12.5, 73]
labels_touch = ["0h (Same bar)", "1-2h", "3-5h", "6-12h", ">12h"]
trades_df["bin_touch_to_entry"] = pd.cut(trades_df["hours_touch_to_entry"], bins=bins_touch, labels=labels_touch, right=True)

# Bins for OB Width %
bins_width = [0, 0.5, 1.0, 1.5, 2.0, 10.0]
labels_width = ["<0.50%", "0.50-1.00%", "1.00-1.50%", "1.50-2.00%", ">2.00%"]
trades_df["bin_width"] = pd.cut(trades_df["ob_width_pct"], bins=bins_width, labels=labels_width, right=True)

def summarize_group(df, group_col):
    rows = []
    for g, gdf in df.groupby(group_col, observed=True):
        n = len(gdf)
        w = int(gdf["is_win"].sum())
        l = n - w
        wr = (w / n * 100.0) if n > 0 else 0.0
        tot_r = float(gdf["realized_r"].sum())
        exp_r = (tot_r / n) if n > 0 else 0.0
        gain_r = float(gdf[gdf["outcome"] == "FILLED_TP"]["realized_r"].sum())
        loss_r = abs(float(gdf[gdf["outcome"] == "FILLED_SL"]["realized_r"].sum()))
        pf = (gain_r / loss_r) if loss_r > 0 else 99.0
        rows.append({
            "Segment": str(g),
            "Trades": n,
            "Wins": w,
            "Losses": l,
            "Win Rate %": round(wr, 2),
            "Total R": round(tot_r, 2),
            "Expectancy (R)": round(exp_r, 4),
            "Profit Factor": round(pf, 2),
        })
    return pd.DataFrame(rows)

print("\n" + "=" * 80)
print("1. TIME FROM OB CREATION TO 25% ENTRY (SETUP -> FILL LATENCY)")
print("=" * 80)
df_s1 = summarize_group(trades_df, "bin_setup_to_entry")
print(df_s1.to_string(index=False))

print("\n" + "=" * 80)
print("2. TIME FROM FIRST ZONE TOUCH TO 25% ENTRY (ABSORPTION LATENCY)")
print("=" * 80)
df_s2 = summarize_group(trades_df, "bin_touch_to_entry")
print(df_s2.to_string(index=False))

print("\n" + "=" * 80)
print("3. ORDER BLOCK WIDTH % (ZONE GEOMETRY)")
print("=" * 80)
df_s3 = summarize_group(trades_df, "bin_width")
print(df_s3.to_string(index=False))

print("\n" + "=" * 80)
print("4. ASSET SEGMENTATION")
print("=" * 80)
df_s4 = summarize_group(trades_df, "asset")
print(df_s4.to_string(index=False))

print("\n" + "=" * 80)
print("5. DIRECTION SEGMENTATION")
print("=" * 80)
df_s5 = summarize_group(trades_df, "direction")
print(df_s5.to_string(index=False))
