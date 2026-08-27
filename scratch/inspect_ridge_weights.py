"""
Scratch script to inspect Phase T Ridge weights and correlation structure.
"""

from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

repo_root = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
df = pd.read_csv(master_path)

feature_cols = [c for c in df.columns if c.startswith("feat_")]
print(f"Number of causal features: {len(feature_cols)}")

# Fit on mature 2024 seed data
df["dec_dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
df["label_mat_dt"] = df["dec_dt"] + pd.to_timedelta(df["holding_bars"], unit="h")
seed_mask = (df["dec_dt"] >= "2024-06-01") & (df["dec_dt"] <= "2024-12-31") & (df["label_mat_dt"] <= "2024-12-31")
seed_df = df[seed_mask]

X_seed = seed_df[feature_cols].values
y_seed = seed_df["realized_r"].values

ridge = Ridge(alpha=1.0, random_state=42)
ridge.fit(X_seed, y_seed)

weights = pd.DataFrame({
    "feature": feature_cols,
    "weight": ridge.coef_,
    "abs_weight": np.abs(ridge.coef_),
}).sort_values(by="abs_weight", ascending=False)

print("\n--- PHASE T RIDGE(alpha=1.0) SEED WEIGHTS ---")
print(weights.to_string(index=False))

# Fit across all mature data to see weight evolution
all_mature = df[df["label_mat_dt"] <= "2026-08-01"]
X_all = all_mature[feature_cols].values
y_all = all_mature["realized_r"].values

ridge_all = Ridge(alpha=1.0, random_state=42)
ridge_all.fit(X_all, y_all)

weights_all = pd.DataFrame({
    "feature": feature_cols,
    "seed_weight": ridge.coef_,
    "full_weight": ridge_all.coef_,
    "stability_sign_match": np.sign(ridge.coef_) == np.sign(ridge_all.coef_),
}).sort_values(by="full_weight", key=abs, ascending=False)

print("\n--- RIDGE WEIGHT EVOLUTION & TEMPORAL STABILITY ---")
print(weights_all.to_string(index=False))
