# QuantEdge AI — Phase G Historical Shadow Replay & Calibration Report

**Generated Date**: 2026-08-25  
**Evaluation Scope**: Full Canonical Historical Dataset (All 4 Instruments: BTCUSD, ETHUSD, SOLUSD, XRPUSD)  
**Total Historical 1H Candles**: 22,332 (5,583 per asset)  
**Total SMC Setups Evaluated**: 1,501  
**Model Name**: `quantedge-ai-v2.onnx` (SHA-256 Verified)  
**Governance Invariant**: `AI_PROMOTION_STATUS = REJECTED` | `live_execution_authorized = false`  

---

## 1. Executive Summary & Production Readiness Verdict

This historical shadow replay evaluated all **1,501 legitimate trade setups** generated across all 4 canonical crypto assets against real Delta Exchange India order book and price data.

Under Phase G shadow execution rules:
- **Shadow Inference Active**: The AI model computes forward expectancy predictions on all setups.
- **Zero Order Dispatch**: Every setup is tagged with `execution_authorized = false` and `governanceStatus = "REJECTED"`.
- **System Invariant Verified**: No Delta Exchange API order placement requests were dispatched.

---

## 2. Multi-Asset Shadow Performance Matrix

| Asset | Total Setups | SMC Base Win Rate | SMC Base Exp (R) | SMC Base PF | AI Filtered Setups | AI Pass Rate | AI Win Rate | AI Exp (R) | AI PF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **BTCUSD** | 334 | 38.0% | +0.1356R | 1.219 | 76 | 22.8% | 82.9% | +1.4868R | 9.692 |
| **ETHUSD** | 356 | 33.7% | -0.0145R | 0.978 | 159 | 44.7% | 53.5% | +0.5810R | 2.265 |
| **SOLUSD** | 430 | 42.1% | +0.2543R | 1.439 | 124 | 28.8% | 81.5% | +1.4132R | 8.619 |
| **XRPUSD** | 381 | 19.7% | -0.4109R | 0.488 | 11 | 2.9% | 100.0% | +2.2112R | nan |
| **GLOBAL (All 4 Assets)** | **1501** | **33.5%** | **-0.0047R** | **0.993** | **370** | **24.7%** | **70.3%** | **+1.0944R** | **4.713** |

---

## 3. 5-Bucket Prediction Calibration Table

Evaluates the monotonicity and predictive alignment between the ONNX model's predicted Realized R (R_pred) and the true forward 72-hour trade outcome.

| Prediction Bucket (R_pred) | Setup Count | % of Setups | Mean Predicted R | Mean Actual Realized R | Actual Win Rate (%) | Mean Actual MFE (R) | Mean Actual MAE (R) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **< 0.00R** | 829 | 55.2% | -0.3818R | -0.5083R | 16.5% | 0.973R | 1.445R |
| **[0.00R, 0.25R)** | 147 | 9.8% | +0.1296R | -0.1620R | 27.9% | 1.189R | 1.351R |
| **[0.25R, 0.50R)** | 155 | 10.3% | +0.3772R | +0.2140R | 41.9% | 1.329R | 1.016R |
| **[0.50R, 1.00R)** | 240 | 16.0% | +0.7443R | +0.8260R | 61.7% | 1.677R | 0.930R |
| **>= 1.00R** | 130 | 8.7% | +1.1790R | +1.5900R | 86.2% | 2.178R | 0.697R |

---

## 4. Order Block Structural Breakdown

| Order Block State | Total Setups | Base SMC Exp | AI Shadow Filtered Setups | AI Filtered Win Rate | AI Filtered Exp |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Fresh Order Blocks** (>= 0.70 strength) | 1361 | -0.0367R | 284 | 72.9% | +1.1816R |
| **Mitigated Order Blocks** (< 0.70 strength) | 140 | +0.3059R | 86 | 61.6% | +0.8066R |

---

## 5. Security & Governance Invariants Confirmation

1. **Deterministic Parity**: Python and Java feature extractors and ONNX inference runtimes maintain numeric parity within <= 10^-4 across all 24 canonical features and 3 output targets.
2. **Strict Non-Authoritative Shadow Invariant**: `AiShadowResult.executionAuthorized` is strictly guarded and hardcoded to `false`.
3. **Execution Lock Integrity**: Any live trade dispatch requires `AI_PROMOTION_STATUS = PROMOTED`. As status is `REJECTED`, the combined decision engine routes all signals directly to `BLOCKED_BY_SYSTEM`.
4. **Zero Live Delta API Calls**: Confirmed 0 Delta Exchange order placement API requests during historical replay and live shadow execution.
