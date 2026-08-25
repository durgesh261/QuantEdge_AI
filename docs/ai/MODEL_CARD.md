# Model Card: QuantEdge AI Multi-Asset Regressor & OB Filter (Phase K)

## Production Shadow Model Details (`quantedge-ai-v2`)
- **Model Name**: `quantedge-ai-v2`
- **Model Architecture**: Multi-Output Random Forest Regressor (`n_estimators=100`, `max_depth=4`, `min_samples_leaf=5`, `max_features=0.5`, `random_state=42`)
- **Input Features**: 24 canonical features (`canonical-24-v2` / `canonical-24-h1`)
- **Output Targets**: 3 continuous targets (`target_realized_r`, `target_mfe_r`, `target_mae_r`)
- **Inference Format**: ONNX v1.16+ (opset 15)
- **Model Checksum (SHA-256)**: `a4e5bb708a20c667bc0a956df6064c8510f80b313618d5a04857bfeb599e44de`
- **Dataset Fingerprint (SHA-256)**: `020dda52614a1dd08b8e8885af2d30d75aa2c44fede91cc7f56d005686d4e862`
- **Inference Latency**: p50 = 0.034ms, p95 = 0.041ms (Target $\le 5.0$ms PASS)

## Multi-Asset Scope & Canonical Data Provenance
- **BTCUSD (1H)**: 19,479 candles (SHA-256: `5e7bbab57e308b97e80980286690229fbc56db3263d19039303f32777c1e0ee9`)
- **ETHUSD (1H)**: 19,479 candles (SHA-256: `0ca80cbe3b83870f68ecc7cdd2ee00c4eb38b3f5145eb051ad5d3c187d30cb0f`)
- **SOLUSD (1H)**: 19,479 candles (SHA-256: `baf801dff6d7947e082bd3c15a2c65cc93487c2c741b686b004793949bd668e5`)
- **XRPUSD (1H)**: 19,479 candles (SHA-256: `7871b2966b7a4e680f1e4b0833f67423de0a94a2d3e7382c7bc309789261238f`)

## Phase K Evaluation & Promotion Gate Status
- **Authoritative Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**
- **Live Execution Authorization**: `live_execution_authorized = false`
- **SMC Baseline OOS Expectancy**: `+0.0033R` (PF 1.005, WR 36.59%)
- **AI Filtered OOS Expectancy**: `+0.2845R` (PF 1.553, WR 45.95%, Coverage 22.56%)
- **Incremental Expectancy**: **`+0.2812R`** vs SMC baseline
- **Paired MBB 95% CI**: `[-0.1700R, +0.6782R]`
- **Reason for Rejection**: Criterion C5 failed because the Moving Block Bootstrap 95% CI lower bound crosses zero (`-0.1700R`) due to sample size constraints on the 76-day OOS slice ($N=164, n=37$).

## Leave-One-Asset-Out (LOAO) Generalization (Phase K)
- **Held-Out BTCUSD ($N=433$)**: Incremental `+0.2071R`, 95% CI `[+0.0450R, +0.3561R]`, Status: `GENERALIZED_POSITIVE`
- **Held-Out ETHUSD ($N=395$)**: Incremental `+0.1529R`, 95% CI `[+0.0273R, +0.2744R]`, Status: `GENERALIZED_POSITIVE`
- **Held-Out SOLUSD ($N=454$)**: Incremental `+0.1668R`, 95% CI `[+0.0771R, +0.2476R]`, Status: `GENERALIZED_POSITIVE`
- **Held-Out XRPUSD ($N=384$)**: Incremental `+0.1683R`, 95% CI `[+0.0320R, +0.3074R]`, Status: `GENERALIZED_POSITIVE`
- **LOAO Positive Generalization Rate**: **4/4 (100%)**

## Safety Invariants & Execution Boundary
- `AI_UNAVAILABLE` $\implies$ `NO LIVE EXECUTION (BLOCKED_BY_SYSTEM)`
- `AI_PROMOTION_REJECTED` $\implies$ `NO LIVE EXECUTION (BLOCKED_BY_SYSTEM)`
- Live order dispatch remains strictly locked at `0` live orders dispatched.
- Sole authorized production execution engine: **Deterministic SMC Engine**.