# Model Card: QuantEdge AI Multi-Asset Regressor & OB Filter (Phase L)

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

## Phase L Powered Chronological OOS Evaluation & Governance Status
- **Authoritative Promotion Decision**: **`AI_PROMOTION_STATUS = REJECTED`**
- **Live Execution Authorization**: `live_execution_authorized = false`
- **Powered Chronological OOS Universe**: $N=863$ trade setups (2025-07-04 to 2026-08-19, 13.5 months)
- **SMC Baseline OOS Expectancy**: `+0.0205R` (PF 1.033, WR 37.78% [34.60%, 41.06%])
- **AI Filtered OOS Expectancy**: `+0.2154R` (PF 1.393, WR 44.52% [36.70%, 52.62%], Coverage 16.92%, $n=146$)
- **Incremental Expectancy**: **`+0.1949R`** vs SMC baseline
- **Rejected Trades Expectancy**: `-0.0192R` (PF 0.970, WR 36.40%, $n=717$)
- **Separation (Accepted vs Rejected)**: **`+0.2346R`**
- **10,000-Resample Paired MBB 95% CI**: `[-0.0024R, +0.4093R]`
- **Reason for Rejection**: Criterion C5 strictly failed because the 10,000-resample Moving Block Bootstrap 95% CI lower bound crossed zero by $-0.0024$R.

## Leave-One-Asset-Out (LOAO) Generalization (Phase L)
- **Held-Out BTCUSD ($N=435$)**: Incremental `+0.3456R`, 95% CI `[-0.0280R, +0.7201R]`, Status: `GENERALIZED_POSITIVE`
- **Held-Out ETHUSD ($N=395$)**: Incremental `-0.0305R`, 95% CI `[-0.2677R, +0.2186R]`, Status: `GENERALIZED_NEUTRAL`
- **Held-Out SOLUSD ($N=453$)**: Incremental `+0.3213R`, 95% CI `[+0.1163R, +0.5271R]`, Status: `GENERALIZED_POSITIVE`
- **Held-Out XRPUSD ($N=384$)**: Incremental `+0.1622R`, 95% CI `[-0.0836R, +0.3778R]`, Status: `GENERALIZED_POSITIVE`
- **LOAO Non-Negative Generalization Rate**: **4/4 (100%)**

## Safety Invariants & Execution Boundary
- `AI_UNAVAILABLE` $\implies$ `NO LIVE EXECUTION (BLOCKED_BY_SYSTEM)`
- `AI_PROMOTION_REJECTED` $\implies$ `NO LIVE EXECUTION (BLOCKED_BY_SYSTEM)`
- Live order dispatch remains strictly locked at `0` live orders dispatched.
- Sole authorized production execution engine: **Deterministic SMC Engine**.