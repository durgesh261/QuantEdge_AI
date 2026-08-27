"""
Scratch script to perform comprehensive empirical bottleneck analysis on Phase T.
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
pred_path = repo_root / "docs" / "ai" / "phase_t_multiyear_predictions.csv"

master_df = pd.read_csv(master_path)
pred_df = pd.read_csv(pred_path)

# Merge predictions with all features and labels
merged = pd.merge(master_df, pred_df[["ob_id", "prediction", "ai_decision"]], on="ob_id")
oos_df = merged[merged["prediction"].notna()].copy()
accepted_df = oos_df[oos_df["ai_decision"] == "ACCEPT"].copy()

print("=" * 80)
print("PHASE T EMPIRICAL BOTTLENECK DIAGNOSTICS")
print("=" * 80)
print(f"Total OOS Population: {len(oos_df)}")
print(f"Accepted Trades: {len(accepted_df)} (Coverage: {len(accepted_df)/len(oos_df)*100:.1f}%)")

# 1. Outcome Breakdown of Accepted Trades
wins = accepted_df[accepted_df["realized_r"] > 0]
losses = accepted_df[accepted_df["realized_r"] <= 0]
print(f"\n--- 1. OUTCOME BREAKDOWN (Accepted N={len(accepted_df)}) ---")
print(f"Wins: {len(wins)} ({len(wins)/len(accepted_df)*100:.2f}%) | Losses: {len(losses)} ({len(losses)/len(accepted_df)*100:.2f}%)")
print(f"Mean Realized R: {accepted_df['realized_r'].mean():+.4f}R")
print(f"Gross Gain: {wins['realized_r'].sum():+.2f}R | Gross Loss: {abs(losses['realized_r'].sum()):.2f}R | PF: {wins['realized_r'].sum()/abs(losses['realized_r'].sum()):.2f}")

# 2. Excursion Dynamics: MFE and MAE
print(f"\n--- 2. EXCURSION DYNAMICS (MFE & MAE in R-multiples) ---")
print(f"ALL ACCEPTED -> Mean MFE: {accepted_df['mfe_r'].mean():.2f}R, Median MFE: {accepted_df['mfe_r'].median():.2f}R, Mean MAE: {accepted_df['mae_r'].mean():.2f}R")
print(f"WINNERS      -> Mean MFE: {wins['mfe_r'].mean():.2f}R, Median MFE: {wins['mfe_r'].median():.2f}R, Mean MAE: {wins['mae_r'].mean():.2f}R, Median MAE: {wins['mae_r'].median():.2f}R")
print(f"LOSERS       -> Mean MFE: {losses['mfe_r'].mean():.2f}R, Median MFE: {losses['mfe_r'].median():.2f}R, Mean MAE: {losses['mae_r'].mean():.2f}R, Median MAE: {losses['mae_r'].median():.2f}R")

# Analysis of Losers with Significant MFE (Premature Stop / TP too far)
losers_mfe_ge_05 = losses[losses["mfe_r"] >= 0.5]
losers_mfe_ge_10 = losses[losses["mfe_r"] >= 1.0]
losers_mfe_ge_15 = losses[losses["mfe_r"] >= 1.5]
print(f"Losers reaching >= +0.5R MFE before dying: {len(losers_mfe_ge_05)} ({len(losers_mfe_ge_05)/len(losses)*100:.1f}%)")
print(f"Losers reaching >= +1.0R MFE before dying: {len(losers_mfe_ge_10)} ({len(losers_mfe_ge_10)/len(losses)*100:.1f}%)")
print(f"Losers reaching >= +1.5R MFE before dying: {len(losers_mfe_ge_15)} ({len(losers_mfe_ge_15)/len(losses)*100:.1f}%)")

# Analysis of Winners penetration depth (how deep do winners go into OB?)
print(f"\n--- 3. OB PENETRATION DISTRIBUTION IN WINNERS (MAE in OB width) ---")
print(f"Winners MAE < 0.25 (shallow touch): {len(wins[wins['mae_r'] < 0.25])} ({len(wins[wins['mae_r'] < 0.25])/len(wins)*100:.1f}%)")
print(f"Winners MAE [0.25, 0.50): {len(wins[(wins['mae_r'] >= 0.25) & (wins['mae_r'] < 0.50)])} ({len(wins[(wins['mae_r'] >= 0.25) & (wins['mae_r'] < 0.50)])/len(wins)*100:.1f}%)")
print(f"Winners MAE [0.50, 0.75): {len(wins[(wins['mae_r'] >= 0.50) & (wins['mae_r'] < 0.75)])} ({len(wins[(wins['mae_r'] >= 0.50) & (wins['mae_r'] < 0.75)])/len(wins)*100:.1f}%)")
print(f"Winners MAE >= 0.75 (deep penetration): {len(wins[wins['mae_r'] >= 0.75])} ({len(wins[wins['mae_r'] >= 0.75])/len(wins)*100:.1f}%)")

# 4. Holding Duration Analysis
print(f"\n--- 4. HOLDING DURATION (Hours) ---")
print(f"Winners Holding Duration -> Mean: {wins['holding_bars'].mean():.1f}h, Median: {wins['holding_bars'].median():.1f}h, 75th: {wins['holding_bars'].quantile(0.75):.1f}h, Max: {wins['holding_bars'].max():.1f}h")
print(f"Losers Holding Duration  -> Mean: {losses['holding_bars'].mean():.1f}h, Median: {losses['holding_bars'].median():.1f}h, 75th: {losses['holding_bars'].quantile(0.75):.1f}h, Max: {losses['holding_bars'].max():.1f}h")
print(f"Quick Stopouts (<= 2h): {len(losses[losses['holding_bars'] <= 2])} ({len(losses[losses['holding_bars'] <= 2])/len(losses)*100:.1f}% of losers)")

# 5. Asset Breakdown in Accepted Setups
print(f"\n--- 5. CROSS-ASSET STATS (Accepted Trades) ---")
for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
    sym_df = accepted_df[accepted_df["asset"] == sym]
    sym_wins = sym_df[sym_df["realized_r"] > 0]
    wr = len(sym_wins) / len(sym_df) * 100 if len(sym_df) > 0 else 0
    exp = sym_df["realized_r"].mean() if len(sym_df) > 0 else 0
    tot_r = sym_df["realized_r"].sum() if len(sym_df) > 0 else 0
    g_gain = sym_wins["realized_r"].sum() if len(sym_wins) > 0 else 0
    g_loss = abs(sym_df[sym_df["realized_r"] <= 0]["realized_r"].sum())
    pf = g_gain / g_loss if g_loss > 0 else 99.0
    print(f"{sym:<7} -> N: {len(sym_df):>3} | WR: {wr:>5.1f}% | Exp: {exp:>+6.4f}R | PF: {pf:>4.2f} | TotR: {tot_r:>+6.2f}R | Mean MFE: {sym_df['mfe_r'].mean():.2f}R | Mean MAE: {sym_df['mae_r'].mean():.2f}R")

# 6. Directional Breakdown
print(f"\n--- 6. DIRECTIONAL STATS (LONG vs SHORT) ---")
for d in ["LONG", "SHORT"]:
    dir_df = accepted_df[accepted_df["direction"] == d]
    dir_wins = dir_df[dir_df["realized_r"] > 0]
    wr = len(dir_wins) / len(dir_df) * 100 if len(dir_df) > 0 else 0
    exp = dir_df["realized_r"].mean() if len(dir_df) > 0 else 0
    tot_r = dir_df["realized_r"].sum() if len(dir_df) > 0 else 0
    print(f"{d:<6} -> N: {len(dir_df):>3} | WR: {wr:>5.1f}% | Exp: {exp:>+6.4f}R | TotR: {tot_r:>+6.2f}R | Mean MFE: {dir_df['mfe_r'].mean():.2f}R | Mean MAE: {dir_df['mae_r'].mean():.2f}R")

# 7. Key Feature Discriminators (Winners vs Losers in Accepted Population)
print(f"\n--- 7. FEATURE DISCRIMINATORS (Winners vs Losers in Accepted Population) ---")
feature_cols = [c for c in oos_df.columns if c.startswith("feat_")]
discrim = []
for col in feature_cols:
    w_vals = wins[col].dropna().values
    l_vals = losses[col].dropna().values
    if len(w_vals) > 0 and len(l_vals) > 0:
        stat, pval = stats.mannwhitneyu(w_vals, l_vals, alternative="two-sided")
        discrim.append({
            "feature": col,
            "win_mean": float(np.mean(w_vals)),
            "loss_mean": float(np.mean(l_vals)),
            "diff": float(np.mean(w_vals) - np.mean(l_vals)),
            "pval": float(pval),
        })

discrim_df = pd.DataFrame(discrim).sort_values(by="pval")
print(discrim_df.head(15).to_string(index=False))

# 8. All OOS Feature Discriminators (All 1,239 setups: profitable setups vs losing setups)
print(f"\n--- 8. GLOBAL DISCRIMINATORS ACROSS ALL 1,239 OOS SETUPS (tp_first=1 vs tp_first=0) ---")
all_w = oos_df[oos_df["tp_first"] == 1]
all_l = oos_df[oos_df["tp_first"] == 0]
discrim_all = []
for col in feature_cols:
    w_vals = all_w[col].dropna().values
    l_vals = all_l[col].dropna().values
    if len(w_vals) > 0 and len(l_vals) > 0:
        stat, pval = stats.mannwhitneyu(w_vals, l_vals, alternative="two-sided")
        discrim_all.append({
            "feature": col,
            "win_mean": float(np.mean(w_vals)),
            "loss_mean": float(np.mean(l_vals)),
            "diff": float(np.mean(w_vals) - np.mean(l_vals)),
            "pval": float(pval),
        })
discrim_all_df = pd.DataFrame(discrim_all).sort_values(by="pval")
print(discrim_all_df.head(15).to_string(index=False))

# 9. Concurrency / Clustering of Accepted Trades
accepted_df["dec_dt"] = pd.to_datetime(accepted_df["decision_timestamp"], utc=True)
accepted_df["exit_dt"] = accepted_df["dec_dt"] + pd.to_timedelta(accepted_df["holding_bars"], unit="h")
accepted_df = accepted_df.sort_values(by="dec_dt")

# Overlapping trades
overlaps = 0
for i in range(len(accepted_df)):
    row = accepted_df.iloc[i]
    active_mask = (accepted_df["dec_dt"] < row["dec_dt"]) & (accepted_df["exit_dt"] > row["dec_dt"])
    overlaps += int(active_mask.sum())

print(f"\n--- 9. CONCURRENCY & PORTFOLIO LOAD ---")
print(f"Total concurrent overlap instances across 288 accepted trades: {overlaps}")
print(f"Mean concurrent positions open at any signal: {overlaps / len(accepted_df):.2f}")

print("\n" + "=" * 80)
