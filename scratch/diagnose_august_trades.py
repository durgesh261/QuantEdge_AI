"""
Inspect candle coverage and match every August 2026 Order Block.
"""

from pathlib import Path
import pandas as pd
import numpy as np

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)

aug_mask = (df["dec_dt"] >= "2026-08-01 00:00:00+00:00") & (df["dec_dt"] <= "2026-08-26 23:59:59+00:00")
aug_obs = df[aug_mask].sort_values(by=["dec_dt", "ob_id"]).reset_index(drop=True)

print(f"Total August OBs: {len(aug_obs)}")

# Load candles for all 4 assets
candles = {}
for asset in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
    c_path = repo_root / "data" / "canonical" / "delta_exchange_india" / asset / "1h" / "2026.csv"
    cdf = pd.read_csv(c_path)
    cdf["dt"] = pd.to_datetime(cdf["timestamp"], utc=True)
    candles[asset] = cdf.sort_values("dt").reset_index(drop=True)
    aug_c = cdf[(cdf["dt"] >= "2026-08-01") & (cdf["dt"] <= "2026-08-26 23:59:59")]
    print(f"{asset}: Total 2026 candles = {len(cdf)}, August candles = {len(aug_c)}, Latest = {cdf['dt'].max()}")

# Inspect each August OB setup
print("\n" + "=" * 100)
print("AUGUST 2026 ORDER BLOCK SETUPS INVENTORY")
print("=" * 100)
for i, ob in aug_obs.iterrows():
    asset = ob["asset"]
    dec_dt = ob["dec_dt"]
    dir_ = ob["direction"]
    top = float(ob["ob_high"])
    bot = float(ob["ob_low"])
    w = top - bot
    w_pct = float(ob["feat_ob_width_pct"])
    
    if dir_ == "LONG":
        entry = top - 0.25 * w
        sl = bot
        tp = entry * 1.008
        risk_dist = entry - sl
    else:
        entry = bot + 0.25 * w
        sl = top
        tp = entry * 0.992
        risk_dist = sl - entry
        
    sl_pct = (risk_dist / entry) * 100.0
    lev = 35.0 / sl_pct if sl_pct > 0 else 0
    tp_ret = 0.80 * lev
    
    mae_r = float(ob["mae_r"])
    mfe_r = float(ob["mfe_r"])
    bars = int(ob["holding_bars"])
    
    print(f"#{i+1:02d} | {dec_dt.strftime('%Y-%m-%d %H:%M')} | {asset:<6} | {dir_:<5} | OB:[{bot:.2f}, {top:.2f}] (W:{w_pct:.2f}%) | Entry:{entry:.2f} | SL:{sl:.2f} (Dist:{sl_pct:.3f}%) | Lev:{lev:5.1f}x | TP_Ret:+{tp_ret:5.1f}% | MAE_R:{mae_r:.2f} | MFE_R:{mfe_r:.2f} | Bars:{bars}")
