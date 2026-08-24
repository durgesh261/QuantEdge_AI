# Model Card: QuantEdge AI v2 Multi-Asset Regressor (Phase E)

## Model Details
- **Model Name**: `quantedge-ai-v2`
- **Model Architecture**: Multi-Output Random Forest Regressor (`n_estimators=100`, `max_depth=6`, `min_samples_leaf=4`)
- **Input Features**: 24 canonical features (`canonical-24-v2`)
- **Output Targets**: 3 continuous targets (`target_realized_r`, `target_mfe_r`, `target_mae_r`)
- **Inference Format**: ONNX v1.16+ (opset 17)
- **Model Checksum (SHA-256)**: `e33cd01f7f8a39db3f3ec4288a569b949d2d9d7a5146674d4f676f9caa124a8b`
- **Inference Latency**: p50 = 0.45ms, p95 = 1.12ms (Target $\le 5.0$ms PASS)

## Multi-Asset Scope & Data Availability
- **BTCUSD (1H)**: Available (5,583 real Delta Exchange India candles)
- **ETHUSD (1H)**: Not Available (No canonical historical data present in repo)
- **SOLUSD (1H)**: Not Available (No canonical historical data present in repo)
- **XRPUSD (1H)**: Not Available (No canonical historical data present in repo)

## Phase E Evaluation & Second Promotion Gate Status
- **Authoritative Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**
- **Frozen Validation Threshold**: `+0.00R`
- **OOS SMC Expectancy**: `-0.0435R`
- **OOS AI Expectancy**: `-0.3438R`
- **Incremental Expectancy 95% CI**: `[-0.9236R, +0.3889R]`

## Safety Invariants & Execution Boundary
- `AI_UNAVAILABLE` $\implies$ `NO LIVE EXECUTION`
- `AI_PROMOTION_REJECTED` $\implies$ `NO LIVE EXECUTION`
- Kill switch and risk limits remain server-side authoritative and cannot be overridden by AI.

## Critical Limitations & Disclaimers
> [!IMPORTANT]
> - **Correlation does not imply causation.**
> - **Historical backtest performance does not guarantee future live performance.**
> - **The AI model does not independently authorize live trading unless governance promotion succeeds.**