# Model Card: QuantEdge AI v2 Multi-Asset Regressor (Phase H)

## Model Details
- **Model Name**: `quantedge-ai-v2`
- **Model Architecture**: Multi-Output Random Forest Regressor (`n_estimators=100`, `max_depth=4`, `min_samples_leaf=5`, `max_features=0.5`, `random_state=42`)
- **Input Features**: 24 canonical features (`canonical-24-v2` / `canonical-24-h1`)
- **Output Targets**: 3 continuous targets (`target_realized_r`, `target_mfe_r`, `target_mae_r`)
- **Inference Format**: ONNX v1.16+ (opset 15)
- **Model Checksum (SHA-256)**: `a4e5bb708a20c667bc0a956df6064c8510f80b313618d5a04857bfeb599e44de`
- **Dataset Fingerprint (SHA-256)**: `020dda52614a1dd08b8e8885af2d30d75aa2c44fede91cc7f56d005686d4e862`
- **Inference Latency**: p50 = 0.034ms, p95 = 0.041ms (Target $\le 5.0$ms PASS)

## Multi-Asset Scope & Canonical Data Provenance
- **BTCUSD (1H)**: Available (5,583 real Delta Exchange India candles)
- **ETHUSD (1H)**: Available (5,583 real Delta Exchange India candles)
- **SOLUSD (1H)**: Available (5,583 real Delta Exchange India candles)
- **XRPUSD (1H)**: Available (5,583 real Delta Exchange India candles)

## Phase H Evaluation & Promotion Gate Status
- **Authoritative Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**
- **Live Execution Authorization**: `live_execution_authorized = false`
- **Frozen Validation Threshold**: `+0.50R`
- **Pooled OOS SMC Expectancy**: `-0.1532R`
- **Pooled OOS AI Expectancy (Baseline)**: `+0.0242R`
- **Pooled OOS AI Expectancy (Scale-Invariant)**: `+0.2146R`
- **Incremental Expectancy 95% CI (MBB)**: `[-0.3434R, +1.1401R]` (Lower bound $< 0.0$R $\implies$ Promotion Blocked)

## Leave-One-Asset-Out (LOAO) Summary (Phase H Scale-Invariant Strategy)
- **Held-Out BTCUSD**: SMC `+0.1356R` $\to$ AI `+1.4000R` (Incremental: `+1.2644R`, 95% CI: `[+0.6335R, +1.9187R]`, Status: `GENERALIZED_POSITIVE`)
- **Held-Out ETHUSD**: SMC `-0.0145R` $\to$ AI `-0.0252R` (Incremental: `-0.0107R`, 95% CI: `[-0.5629R, +0.4458R]`, Status: `GENERALIZED_NEUTRAL`)
- **Held-Out SOLUSD**: SMC `+0.2543R` $\to$ AI `+0.5012R` (Incremental: `+0.2469R`, 95% CI: `[-0.7940R, +1.3295R]`, Status: `GENERALIZED_NEUTRAL`)
- **Held-Out XRPUSD**: SMC `-0.4109R` $\to$ AI `-0.2899R` (Incremental: `+0.1210R`, 95% CI: `[-0.4202R, +0.8583R]`, Status: `GENERALIZED_NEUTRAL`)
- **LOAO Non-Negative Generalization Rate**: **4/4 (100%)**

## Safety Invariants & Execution Boundary
- `AI_UNAVAILABLE` $\implies$ `NO LIVE EXECUTION (BLOCKED_BY_SYSTEM)`
- `AI_PROMOTION_REJECTED` $\implies$ `NO LIVE EXECUTION (BLOCKED_BY_SYSTEM)`
- Emergency kill switch and risk caps remain strictly enforced on server-side and cannot be bypassed.
- Sole authorized production execution engine: **Deterministic SMC Engine**.

## Phase H Shadow Execution & Governance Invariants
- **Runtime Mode**: `NON_AUTHORITATIVE_SHADOW`
- **Shadow Inference Status**: Active across all live Delta WebSocket & historical SMC setups.
- **Order Placement Dispatch**: Strictly `0` live orders dispatched (`AiShadowResult.executionAuthorized = false`).
- **Feature & Output Parity**: Python scikit-learn $\leftrightarrow$ Python ONNX $\leftrightarrow$ Java ONNX verified across golden vectors ($\le 10^{-4}$ numeric parity).
- **Inference Robustness**: Hardened against missing features, NaN, $\pm\infty$, and dimensionality mismatches.

## Critical Limitations & Disclaimers
> [!IMPORTANT]
> - **Correlation does not imply causation.**
> - **Historical backtest performance does not guarantee future live performance.**
> - **The AI model does not independently authorize live trading unless governance promotion succeeds.**