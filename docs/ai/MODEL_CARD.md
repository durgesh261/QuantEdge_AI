# Model Card: QuantEdge AI v2 Multi-Asset Regressor (Phase F)

## Model Details
- **Model Name**: `quantedge-ai-v2`
- **Model Architecture**: Multi-Output Random Forest Regressor (`n_estimators=100`, `max_depth=8`, `min_samples_leaf=3`)
- **Input Features**: 24 canonical features (`canonical-24-v2`)
- **Output Targets**: 3 continuous targets (`target_realized_r`, `target_mfe_r`, `target_mae_r`)
- **Inference Format**: ONNX v1.16+ (opset 17)
- **Model Checksum (SHA-256)**: `e33cd01f7f8a39db3f3ec4288a569b949d2d9d7a5146674d4f676f9caa124a8b`
- **Inference Latency**: p50 = 0.45ms, p95 = 1.12ms (Target $\le 5.0$ms PASS)

## Multi-Asset Scope & Data Availability
- **BTCUSD (1H)**: Available (5,583 real Delta Exchange India candles)
- **ETHUSD (1H)**: Available (5,583 real Delta Exchange India candles)
- **SOLUSD (1H)**: Available (5,583 real Delta Exchange India candles)
- **XRPUSD (1H)**: Available (5,583 real Delta Exchange India candles)

## Phase F Evaluation & Second Promotion Gate Status
- **Authoritative Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**
- **Frozen Validation Threshold**: `+0.50R`
- **Pooled OOS SMC Expectancy**: `-0.1532R`
- **Pooled OOS AI Expectancy**: `+0.0242R`
- **Incremental Expectancy 95% CI**: `[-0.3239R, +0.9932R]`

## Leave-One-Asset-Out (LOAO) Summary
- **Held-Out BTCUSD**: SMC `+0.1356R` $\to$ AI `-1.0000R` (Incremental: `-1.1356R`, Status: `GENERALIZED_NEGATIVE`)
- **Held-Out ETHUSD**: SMC `-0.0145R` $\to$ AI `+0.0133R` (Incremental: `+0.0278R`, Status: `GENERALIZED_NEUTRAL`)
- **Held-Out SOLUSD**: SMC `+0.2543R` $\to$ AI `+0.0000R` (Incremental: `-0.2543R`, Status: `GENERALIZED_NEGATIVE`)
- **Held-Out XRPUSD**: SMC `-0.4109R` $\to$ AI `-0.6765R` (Incremental: `-0.2656R`, Status: `GENERALIZED_NEGATIVE`)

## Safety Invariants & Execution Boundary
- `AI_UNAVAILABLE` $\implies$ `NO LIVE EXECUTION`
- `AI_PROMOTION_REJECTED` $\implies$ `NO LIVE EXECUTION`
- Kill switch and risk limits remain server-side authoritative and cannot be overridden by AI.

## Critical Limitations & Disclaimers
> [!IMPORTANT]
> - **Correlation does not imply causation.**
> - **Historical backtest performance does not guarantee future live performance.**
> - **The AI model does not independently authorize live trading unless governance promotion succeeds.**