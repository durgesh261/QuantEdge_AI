"""
Summary of Multi-Pair Concurrent Backtest for August 2026.
"""

from pathlib import Path
import pandas as pd

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")

df_5d = pd.read_csv(repo_root / "docs" / "ai" / "last_5_days_multipair_concurrent_trades.csv")
df_full = pd.read_csv(repo_root / "docs" / "ai" / "august_2026_multipair_concurrent_trades.csv")

print("=" * 80)
print("LAST 5 DAYS (AUG 21–26, 2026) MULTI-PAIR SUMMARY")
print("=" * 80)
print(f"Total Trades: {len(df_5d)}")
w_5d = df_5d[df_5d["outcome"] == "FILLED_TP"]
l_5d = df_5d[df_5d["outcome"] == "FILLED_SL"]
print(f"Wins: {len(w_5d)} ({len(w_5d)/len(df_5d)*100:.1f}%) | Losses: {len(l_5d)}")
print(f"Starting Capital: $10.00")
print(f"Gross Ending Capital: ${df_5d.iloc[-1]['ending_capital_gross']:.4f} (+{(df_5d.iloc[-1]['ending_capital_gross']-10)/10*100:.2f}%)")
print(f"Net Ending Capital:   ${df_5d.iloc[-1]['ending_capital_net']:.4f} (+{(df_5d.iloc[-1]['ending_capital_net']-10)/10*100:.2f}%)")
print(f"Total Realized R: {df_5d['realized_r'].sum():+.2f}R | Expectancy: {df_5d['realized_r'].mean():+.4f}R")

print("\n" + "=" * 80)
print("FULL MONTH (AUG 1–26, 2026) MULTI-PAIR SUMMARY")
print("=" * 80)
print(f"Total Trades: {len(df_full)}")
w_full = df_full[df_full["outcome"] == "FILLED_TP"]
l_full = df_full[df_full["outcome"] == "FILLED_SL"]
print(f"Wins: {len(w_full)} ({len(w_full)/len(df_full)*100:.1f}%) | Losses: {len(l_full)}")
print(f"Starting Capital: $10.00")
print(f"Gross Ending Capital: ${df_full.iloc[-1]['ending_capital_gross']:.4f}")
print(f"Net Ending Capital:   ${df_full.iloc[-1]['ending_capital_net']:.4f}")
print(f"Total Realized R: {df_full['realized_r'].sum():+.2f}R | Expectancy: {df_full['realized_r'].mean():+.4f}R")
