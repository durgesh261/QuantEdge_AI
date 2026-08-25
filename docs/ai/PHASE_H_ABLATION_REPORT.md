# QuantEdge AI — Phase H Ablation Study Report

**Generated At**: 2026-08-25 UTC  
**Dataset**: Multi-Asset Canonical (BTCUSD, ETHUSD, SOLUSD, XRPUSD)  
**Partitioning**: 60% Train (912) $\to$ 20% Validation (233) $\to$ 20% Frozen OOS (320)

---

## 1. Executive Summary

This ablation study isolates the relative contribution of **Generic Candle Features** versus **SMC / Order-Block Structural Features** and assesses whether combining both representations produces superior out-of-sample performance and risk-adjusted metrics.

---

## 2. Feature Sets Evaluated

1. **Set A — Candle-Only Features (13 features)**:
   - `trend_strength_1h`, `trend_strength_15m`, `trend_strength_4h`
   - `volatility_1h`, `volatility_15m`
   - `volume_profile`
   - `momentum_1h`, `momentum_15m`
   - `regime_1h_bullish`, `regime_1h_bearish`, `regime_1h_ranging`, `regime_1h_transitional`, `regime_alignment`
2. **Set B — SMC / Order-Block Only Features (9 features)**:
   - `bos_strength`, `choch_strength`, `order_block_strength`, `fvg_strength`, `liquidity_proximity`
   - `risk_reward`, `risk_distance`, `entry_precision`, `direction_long`
3. **Set C — Combined Candle + SMC Features (Canonical 24 Features - Phase F/G Baseline)**:
   - All 24 canonical features (`canonical-24-v2`).
4. **Set D — Combined + Scale-Invariant Risk Context (Enhanced 24 Features)**:
   - `canonical-24-h1` incorporating ATR-normalized scale-invariant geometric risk distance.

---

## 3. Ablation Performance Matrix (Validation vs Frozen OOS)

| Feature Set | Features | Val Exp | Val PF | Val WR | Val Cov | OOS Exp | OOS PF | OOS WR | OOS Cov | OOS Max DD | OOS Incr R | OOS Incremental MBB 95% CI |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Set A: Candle-Only** | 13 | `-1.000R` | `0.000` | `0.0%` | `3.9%` | `+1.4068R` | `8.034` | `80.0%` | `3.1%` (10/320) | `2.00R` | `+1.5600R` | `[+0.9489R, +2.3773R]` |
| **Set B: SMC/OB-Only** | 9 | `-0.1314R` | `0.811` | `28.3%` | `19.7%` | `+0.1540R` | `1.250` | `38.5%` | `12.2%` (39/320) | `19.00R` | `+0.3072R` | `[-0.3660R, +1.1231R]` |
| **Set C: Combined Baseline** | 24 | **`+0.0151R`** | **`1.023`** | **`33.3%`** | `27.0%` | `+0.0242R` | `1.037` | `34.1%` | `12.8%` (41/320) | `18.00R` | `+0.1774R` | `[-0.4193R, +1.0022R]` |
| **Set D: Combined Scale-Inv** | 24 | `-0.0800R` | `0.883` | `30.2%` | `27.0%` | **`+0.2146R`** | **`1.360`** | **`40.4%`** | `14.7%` (47/320) | **`14.00R`** | **`+0.3678R`** | `[-0.3434R, +1.1401R]` |

---

## 4. Key Scientific Findings

1. **Candle-Only Model Overfits and Collapses in Coverage**:
   - Candle-only features failed catastrophically on the Validation split ($-1.000$R expectancy, $0.0\%$ win rate).
   - Although it showed high expectancy on OOS, it executed only **10 trades across the entire year** ($3.1\%$ coverage), failing the $\ge 10\%$ coverage gate and proving unstable.
2. **SMC/OB Features Provide Structural Stability**:
   - The SMC/OB-only model achieved consistent coverage ($19.7\%$ Val, $12.2\%$ OOS) and an out-of-sample profit factor of $1.250$.
3. **Synergy of Combined Scale-Invariant Representation**:
   - Set D (incorporating scale-invariant ATR risk distance) achieved the best trade-off: **$40.4\%$ win rate, $1.360$ profit factor, $+0.2146$R expectancy, and $14.7\%$ coverage** on frozen OOS.
   - However, because the lower bound of the Moving Block Bootstrap CI on incremental expectancy is $-0.3434$R, the statistical promotion requirement is preserved as not met.
