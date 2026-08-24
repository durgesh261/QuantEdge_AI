# Model Card: QuantEdge AI v2 Regressor

## Model Details
- **Model Name**: QuantEdge AI Multi-Output Random Forest Regressor
- **Model Version**: `quantedge-ai-v2`
- **Artifact Path**: `backend/src/main/resources/models/quantedge-ai-v2.onnx`
- **Architecture**: Multi-Output Scikit-Learn Random Forest Regressor (`n_estimators=100`, `max_depth=8`, `opset=15`)
- **Input Features**: Exactly 24 Canonical Features (Order Block, FVG, Structure, Multi-Timeframe Trend/Vol, Regime, Account Context)
- **Target Outputs**: Continuous Float Vectors `[target_realized_r, target_mfe_r, target_mae_r]`

## Training & Data
- **Source**: Delta Exchange India Historical BTCUSD 1H (`data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv`)
- **Range**: 2026-01-01 to 2026-08-21 (5,583 candles)
- **Splits**: 3-Way Purged Chronological (Train: 212, Val: 41, OOS Test: 69)
- **Purge/Embargo Window**: ≥ 72 Hours (Actual: 174h / 150h)

## Evaluation & Performance
- **Validation Win Rate**: 61.9% (Coverage: 51.2%)
- **Out-of-Sample Win Rate**: 29.4% (Coverage: 49.3%)
- **Out-of-Sample Mean R**: -0.1176R (vs SMC: -0.0435R)
- **ONNX Runtime Parity**: Max difference ≤ 5.69e-07

## Safety & Production Authority
- **Production Promotion Status**: **`REJECTED`**
- **Execution Invariant**: `AI_UNAVAILABLE` → `NO LIVE EXECUTION`
- **Risk Engine Authority**: Risk Engine remains server-side authoritative; AI cannot override kill switch or risk limits.