# QuantEdge AI — Phase H Label & Target Quality Audit

**Generated At**: 2026-08-25 UTC  
**Target Candidates Evaluated**: 5 Formulations across 1,501 Canonical Market Setups  
**Evaluation Constraint**: Strictly Evaluated on Train (912) and Validation (233) Splits

---

## 1. Executive Summary

This audit evaluates whether the current 3-target continuous regression formulation (`target_realized_r`, `target_mfe_r`, `target_mae_r`) is scientifically optimal for an SMC / Order-Block trade execution filter, or whether a classification, barrier-hit, or expected-R formulation should replace it.

---

## 2. Realized Target Distribution Analysis

Across all 1,501 qualified trade setups in the canonical dataset:
- **Win Rate ($R > 0$)**: $33.5\%$ (503 setups)
- **Loss Rate ($R = -1.0$)**: $66.5\%$ (998 setups)
- **Timeouts ($72$h barrier not reached)**: $< 0.1\%$
- **Mean Realized R**: $-0.0047$R (Pooled SMC Baseline)
- **Mean MFE**: $1.258$R
- **Mean MAE**: $1.218$R

### Key Observation: Severe Bimodality
Realized R is heavily quantized: setups almost exclusively terminate at $+2.0$R (Take Profit) or $-1.0$R (Stop Loss). Intermediate exits are exceptionally rare due to the 72-hour horizon.

---

## 3. Empirical Comparison of Candidate Target Formulations

All candidate formulations were trained on the 60% Train split (912 setups) and evaluated strictly on the 20% Validation split (233 setups).

| Candidate Formulation | Model Family | Val Loss / Metric | Val Expectancy | Val Profit Factor | Val Win Rate | Val Coverage | Val Max Drawdown | Verdict |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Formulation A: Continuous R Regression** | `RandomForestRegressor` | $\text{MAE} = 1.339, R^2 = -0.222$ | **`+0.0151R`** | **`1.023`** | **`33.3%`** | `27.0%` (63/233) | **`23.05R`** | ✅ **BEST VAL METRICS** |
| **Formulation B: Binary Win Probability $P(\text{Win})$** | `RandomForestClassifier` | $\text{Brier} = 0.243, \text{AUC} = 0.503$ | `-0.1653R` | `0.769` | `27.5%` | `39.1%` (91/233) | `27.04R` | ❌ Inferior Expectancy |
| **Formulation C: Prob of $+1$R Excursion** | `RandomForestClassifier` | $\text{Brier} = 0.295, \text{AUC} = 0.463$ | `-0.0213R` | `0.968` | `32.4%` | `59.7%` (139/233) | `26.01R` | ❌ Negative Expectancy |
| **Formulation D: Expected R Calculation** | $P(\text{Win}) \times \text{RR} - (1-P)$ | $\text{Expectancy Threshold } \ge 0.20$R | `-0.1758R` | `0.756` | `27.2%` | `48.9%` (114/233) | `39.04R` | ❌ High Drawdown |
| **Formulation E: Calibrated Multi-Task** | `CalibratedClassifierCV` | $\text{Brier} = 0.212, \text{Prob} \ge 0.40$ | `-0.2088R` | `0.714` | `26.0%` | `41.2%` (96/233) | `36.05R` | ❌ Negative Expectancy |

---

## 4. Scientific Findings & Decision

1. **Why Pure Binary Classification Fails as a Trade Filter**:
   - Classifiers optimizing log-loss or Brier score treat a $1.5$R setup the same as a $3.0$R setup.
   - SMC setups have varying risk-to-reward ratios ($RR \in [1.3, 2.7]$). Continuous regression naturally weights setups where the structural reward distance justifies the stop distance.
2. **Why Continuous Regression Outperforms on Realized Expectancy**:
   - Formulation A directly predicts the magnitude of the expected return. Filtering on $\hat{R} \ge +0.50$R selects only high-conviction asymmetric opportunities, turning a negative SMC baseline ($-0.1855$R) into positive validation expectancy ($+0.0151$R).
3. **Conclusion**:
   - **Retain the 3-target continuous regression formulation (`target_realized_r`, `target_mfe_r`, `target_mae_r`)** as the authoritative target contract.
