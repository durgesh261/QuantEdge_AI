# QuantEdge AI — Phase H Baseline & Comprehensive Technical Diagnosis

**Generated At**: 2026-08-25 UTC  
**Evaluation Scope**: Full Multi-Asset Canonical Suite (BTCUSD, ETHUSD, SOLUSD, XRPUSD)  
**Governance State**: `AI_PROMOTION_STATUS = REJECTED` (Enforced Shadow Mode, Zero Live Execution)

---

## 1. Executive Summary

Phase H investigates whether the AI's out-of-sample predictive value and cross-asset transferability can be improved for **real SMC / Order-Block trading setups** without compromising existing production safety gates.

This diagnosis establishes the exact mathematical formulation, sample lifecycle, input representation, target mechanics, and the root causes behind Phase F's Leave-One-Asset-Out (LOAO) failures.

---

## 2. Technical Diagnosis of Current Pipeline

### A. Prediction Target Formulation
- **Output Dimensions (3 continuous outputs)**:
  1. `target_realized_r`: Realized R-multiple from forward candle replay ($+RR \approx 2.0$R if TP reached, $-1.0$R if SL breached, or $(P_{\text{exit}} - P_{\text{entry}})/\text{RiskDistance}$ on 72-bar horizon timeout).
  2. `target_mfe_r`: Maximum Favorable Excursion in R-units $\frac{\max(P - P_{\text{entry}})}{\text{RiskDistance}} \ge 0.0$.
  3. `target_mae_r`: Maximum Adverse Excursion in R-units $\frac{\max(P_{\text{entry}} - P)}{\text{RiskDistance}} \ge 0.0$.
- **Horizon**: Exactly 72 bars (72 hours on 1H timeframe), or earliest barrier hit.
- **Tie-Breaker Invariant**: If both Take-Profit and Stop-Loss boundaries are intersected during the same 1H candle, a conservative Stop-Loss first execution is enforced ($-1.0$R).

### B. Sample Unit & Trigger Event
- **Observation Unit**: A qualified trade setup emitted by `StrategyEngine.evaluate_state()`.
- **Qualifying State**: `decision.setup_state` $\in \{\text{TRADE\_SETUP\_READY}, \text{QUALIFIED\_LONG}, \text{QUALIFIED\_SHORT}\}$ with non-null `entry`, `stop_loss`, and `take_profit`.
- **Trigger**: Occurs when price action enters an active, unmitigated Order Block (OB) supported by confirmed structural breaks (BOS/CHOCH) and multi-lookback trend alignment.

### C. Input Feature Representation (`canonical-24-v2`)
The 24 input features span 6 canonical groups:
1. **SMC Structural (Indices 0–4)**:
   - `bos_strength`: Relative magnitude of latest Break of Structure within 20 bars.
   - `choch_strength`: 0.85 if Change of Character, 0.40 otherwise.
   - `order_block_strength`: Inverted OB width normalized by price span.
   - `fvg_strength`: Proximity of close to OB bottom/top.
   - `liquidity_proximity`: Distance to nearest swing/internal pivot within 30 bars.
2. **Market Context (Indices 5–12)**:
   - `trend_strength_1h`, `trend_strength_15m`, `trend_strength_4h`: Multi-lookback price differences.
   - `volatility_1h`, `volatility_15m`: ATR-normalized price ranges.
   - `volume_profile`: 5-bar / 30-bar volume ratio.
   - `momentum_1h`, `momentum_15m`: 10-bar and 3-bar rate of change.
3. **Setup Geometry (Indices 13–15)**:
   - `risk_reward`: Target reward / risk ratio.
   - `risk_distance`: Stop distance in absolute quote currency.
   - `entry_precision`: Entry proximity to OB boundary.
4. **Account & Risk Context (Indices 16–17)**:
   - `account_utilization`: Model portfolio margin utilization ($0.20$).
   - `leverage_ratio`: Calculated leverage / 100.
5. **1H Regime One-Hot (Indices 18–21)**:
   - `regime_1h_bullish`, `regime_1h_bearish`, `regime_1h_ranging`, `regime_1h_transitional`.
6. **Binary Flags (Indices 22–23)**:
   - `regime_alignment`: Agreement between 1H and 4H trend.
   - `direction_long`: 1.0 for Long/Buy, 0.0 for Short/Sell.

### D. Causal Leakage Safeguards & Split Boundaries
- **Causality**: Features at bar $T$ reference only information available at $\le T$.
- **Purged 3-Way Split**: 60% Train (912 setups), 20% Validation (233 setups), 20% Frozen OOS (320 setups).
- **Embargo Window**: Mandatory 72-hour purge gap between Train $\to$ Val and Val $\to$ OOS to eliminate forward trade overlap contamination.
- **Clustering**: Setups occurring within $\le 3$ hours in the same direction and symbol are clustered (37.3% reduction in duplicate exposures).

---

## 3. Phase H Baseline Metrics (Reproduced)

| Metric | SMC Baseline | Phase F/G AI Baseline | Incremental Impact |
|---|---:|---:|---:|
| **Total OOS Setups** | 320 | 320 | — |
| **Executed Setups** | 320 | 41 | `12.8% coverage` |
| **Win Rate** | 28.4% (91/320) | 34.1% (14/41) | `+5.7%` |
| **Expectancy (Mean R)** | -0.1532R | +0.0242R | `+0.1774R` |
| **Profit Factor** | 0.786 | 1.037 | `+0.251` |
| **Max Drawdown** | 56.99R | 18.00R | `-38.99R` |
| **Incremental MBB 95% CI** | — | `[-0.4193R, +1.0022R]` | Lower bound $< 0.0$R |
| **LOAO Pass Rate** | — | 1/4 (25%) | 3 assets negative |

### LOAO Baseline Matrix (Phase F Reproduction)
- **BTCUSD**: SMC `+0.1356R` $\to$ AI `-1.0000R` (Incremental: `-1.1356R`, Status: `GENERALIZED_NEGATIVE`)
- **ETHUSD**: SMC `-0.0145R` $\to$ AI `+0.0133R` (Incremental: `+0.0278R`, Status: `GENERALIZED_NEUTRAL`)
- **SOLUSD**: SMC `+0.2543R` $\to$ AI `+0.0000R` (Incremental: `-0.2543R`, Status: `GENERALIZED_NEGATIVE`)
- **XRPUSD**: SMC `-0.4109R` $\to$ AI `-0.6765R` (Incremental: `-0.2656R`, Status: `GENERALIZED_NEGATIVE`)

---

## 4. Root Cause Analysis: Representation Mismatch & Scale Distortion

1. **Massive Cross-Asset Scale Disparity (`risk_distance`)**:
   - `risk_distance` was fed in raw quote currency. In BTC, mean risk distance is $\approx \$500$, while in XRP it is $\approx \$0.02$.
   - When training on ETH, SOL, XRP and testing on BTC, decision trees split on risk distance $> \$50$, treating BTC as extreme outliers and filtering out nearly 100% of setups.
2. **Representation Mismatch (Candle Momentum vs OB Micro-Structure)**:
   - 33% of features were generic momentum/trend slopes that fluctuated with macro market swings rather than reflecting Order Block quality (freshness, displacement volume, ATR-normalized width, FVG confluence).
3. **Target Bimodality & Regression MSE Loss**:
   - Trades are heavily bimodal: $28\%$ hit $+2.0$R and $72\%$ hit $-1.0$R. Standard MSE regression flattens predictions toward $-0.15$R, requiring a high threshold ($+0.50$R) to execute trades, which severely drops coverage.
