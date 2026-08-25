# QuantEdge AI — Phase H Model Research & Multi-Candidate Benchmark Report

**Generated At**: 2026-08-25 UTC  
**Dataset**: Multi-Asset Canonical (BTCUSD, ETHUSD, SOLUSD, XRPUSD)  
**Partitioning**: 60% Train (912 setups) $\to$ 20% Validation (233 setups)  
**Hardware & Latency Standard**: CPU ONNX Runtime Inference ($\le 5.0$ms Target)

---

## 1. Executive Summary

This report documents the empirical evaluation of 8 diverse machine learning model architectures benchmarked on real SMC / Order-Block setups to select the optimal model family for shadow deployment.

---

## 2. Benchmark Evaluation Matrix (Validation Split)

| Model Candidate | Family | Hyperparameters | Val Score | Val Expectancy | Val Profit Factor | Val Win Rate | Val Coverage | Val Max Drawdown | Verdict |
|---|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Random Forest Regressor** | Tree Ensemble | `n_est=100, max_depth=4, leaf=5, max_feat=0.5` | $R^2 = -0.222$ | **`+0.0151R`** | **`1.023`** | **`33.3%`** | `27.0%` (63/233) | **`23.05R`** | 🏆 **TOP PERFORMER** |
| **Extra Trees Regressor** | Extremely Randomized | `n_est=100, max_depth=6, leaf=5, max_feat=0.6` | $R^2 = -0.166$ | `-0.3070R` | `0.594` | `22.5%` | `21.0%` (49/233) | `20.05R` | ❌ Severe Underperformance |
| **HistGradientBoosting Regressor** | Gradient Boosted Trees | `max_iter=50, max_depth=4, leaf=10` | $R^2 = -0.156$ | `-0.1340R` | `0.809` | `28.3%` | `25.8%` (60/233) | `21.04R` | ❌ Negative Expectancy |
| **Gradient Boosting Regressor** | Sequential Boosting | `n_est=50, max_depth=3, leaf=5` | $R^2 = -0.345$ | `-0.1545R` | `0.782` | `27.7%` | `27.9%` (65/233) | `23.04R` | ❌ Overfitting on Train |
| **Random Forest Classifier** | Tree Classifier | `n_est=100, max_depth=4, leaf=5, max_feat=0.5` | $\text{AUC} = 0.503$ | `-0.1685R` | `0.765` | `27.4%` | `48.5%` (113/233) | `39.04R` | ❌ High Drawdown |
| **Extra Trees Classifier** | Tree Classifier | `n_est=100, max_depth=4, leaf=5, max_feat=0.5` | $\text{AUC} = 0.498$ | `-0.0828R` | `0.880` | `30.3%` | `46.8%` (109/233) | `31.03R` | ❌ Negative Expectancy |
| **HistGradientBoosting Classifier** | Boosted Classifier | `max_iter=50, max_depth=3, leaf=10` | $\text{AUC} = 0.483$ | `-0.1465R` | `0.793` | `28.1%` | `38.2%` (89/233) | `27.04R` | ❌ Poor Discrimination |
| **Calibrated Logistic Regression** | Linear / Sigmoid | `C=0.1, L2 penalty, cv=3` | $\text{AUC} = 0.601$ | `-0.2024R` | `0.712` | `25.0%` | `8.6%` (20/233) | `10.05R` | ❌ Insufficient Coverage |

---

## 3. Comparative Architecture Insights

1. **Why Random Forest Regressor Wins**:
   - Random Forest with aggressive feature subsampling (`max_features=0.50`) and shallow depth (`max_depth=4`) exhibits the lowest variance and highest resistance to noisy financial labels.
   - It is the **only model candidate** that delivered positive validation expectancy ($+0.0151$R) and a profit factor $> 1.00$.
2. **Gradient Boosting Vulnerability**:
   - Boosted trees (GBR and HistGB) greedily minimize residual loss on the training set, causing them to over-index on isolated large excursions in specific assets and fail out-of-sample.
3. **Linear & Logistic Models**:
   - While Logistic Regression achieved an AUC of $0.601$, its calibrated probabilities were compressed, yielding only $8.6\%$ coverage on validation (failing the $\ge 10.0\%$ minimum coverage gate).
