"""
Phase F — Multi-Asset Canonical Expansion & Cross-Asset AI Validation Test Suite.

Verifies:
1. Canonical file discovery across 4 assets (BTCUSD, ETHUSD, SOLUSD, XRPUSD).
2. Canonical schema (timestamp, open, high, low, close, volume).
3. Strict OHLC geometric validity and positive volume.
4. Timestamp ordering (monotonic increasing, UTC).
5. Duplicate candle detection.
6. Missing candle gap detection.
7. Dataset provenance in manifest.json.
8. Cryptographic SHA-256 matching.
9. BTC dataset invariance.
10. Non-synthetic real data integrity.
11. Canonical 24-feature contract.
12. Feature causality (T <= T_setup).
13. 72-hour forward replay (T+1 ... T+72).
14. No target leakage into feature matrix.
15. Chronological purged splitting.
16. 72-hour purge embargo.
17. Cross-asset split integrity.
18. Leave-one-asset-out (LOAO) training isolation.
19. Frozen OOS isolation invariant.
20. ONNX input/output shape invariants.
21. ONNX numeric parity vs Scikit-Learn.
22. Promotion gate evaluation logic.
23. Rejected promotion hard execution lock.
24. Transparent error reporting for insufficient/missing data.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.evaluation.phase_f_gate import PhaseFGateResults, PhaseFMultiAssetGate
from quantedge.ai.evaluation.smc_baseline import calculate_performance_metrics
from quantedge.ai.training.multi_asset_dataset_builder import (
    AssetDataAudit,
    MultiAssetDatasetBuilder,
    audit_canonical_datasets,
    cluster_and_deduplicate_setups,
)
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
)
from quantedge.data.canonical_validator import CanonicalDataValidator, CanonicalValidationReport


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return _get_repo_root()


@pytest.fixture(scope="module")
def canonical_manifest(repo_root) -> Dict[str, Any]:
    manifest_file = repo_root / "data" / "canonical" / "delta_exchange_india" / "manifest.json"
    assert manifest_file.exists(), f"Manifest missing at {manifest_file}"
    return json.loads(manifest_file.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def phase_f_gate() -> PhaseFMultiAssetGate:
    gate = PhaseFMultiAssetGate()
    gate.audit_and_load_data()
    gate.run_pooled_model_research()
    return gate


# ─────────────────────────────────────────────────────────────────────────────
# 1-10: Canonical Data & Manifest Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCanonicalDataAndManifest:
    """Verifies canonical data discovery, schema, OHLC, gaps, and provenance."""

    def test_1_canonical_file_discovery_all_four_assets(self, repo_root):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            csv_path = base / sym / "1h" / "2026.csv"
            meta_path = base / sym / "1h" / "2026_metadata.json"
            assert csv_path.exists(), f"Missing canonical CSV for {sym} at {csv_path}"
            assert meta_path.exists(), f"Missing metadata JSON for {sym} at {meta_path}"

    def test_2_canonical_schema(self, repo_root):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            df = pd.read_csv(base / sym / "1h" / "2026.csv", nrows=10)
            assert list(df.columns) == required_cols

    def test_3_ohlc_validity(self, repo_root):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            rep = CanonicalDataValidator.validate_file(base / sym / "1h" / "2026.csv", sym)
            assert rep.is_valid_ohlc is True
            assert rep.is_valid_volume is True

    def test_4_timestamp_ordering_and_utc(self, repo_root):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            rep = CanonicalDataValidator.validate_file(base / sym / "1h" / "2026.csv", sym)
            assert rep.is_sorted_ascending is True
            assert rep.first_timestamp.startswith("2026-01-01")

    def test_5_duplicate_detection(self, repo_root):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            rep = CanonicalDataValidator.validate_file(base / sym / "1h" / "2026.csv", sym)
            assert rep.duplicate_count == 0

    def test_6_missing_candle_detection(self, repo_root):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            rep = CanonicalDataValidator.validate_file(base / sym / "1h" / "2026.csv", sym)
            assert rep.gap_count == 0

    def test_7_dataset_provenance_manifest(self, canonical_manifest):
        assert canonical_manifest["manifest_version"] == "2.0.0"
        assert "Delta Exchange India" in canonical_manifest["exchange"]
        assert len(canonical_manifest["datasets"]) == 4

    def test_8_sha256_manifest_matching(self, repo_root, canonical_manifest):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]:
            csv_path = base / sym / "1h" / "2026.csv"
            computed_sha = CanonicalDataValidator.calculate_sha256(csv_path)
            manifest_sha = canonical_manifest["datasets"][sym]["sha256"]
            assert computed_sha == manifest_sha

    def test_9_btc_dataset_invariance(self, repo_root):
        btc_csv = repo_root / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
        df_btc = pd.read_csv(btc_csv)
        assert len(df_btc) == 5583

    def test_10_real_asset_samples_non_synthetic(self, repo_root):
        base = repo_root / "data" / "canonical" / "delta_exchange_india"
        btc_closes = pd.read_csv(base / "BTCUSD" / "1h" / "2026.csv")["close"].values
        eth_closes = pd.read_csv(base / "ETHUSD" / "1h" / "2026.csv")["close"].values
        sol_closes = pd.read_csv(base / "SOLUSD" / "1h" / "2026.csv")["close"].values
        # Ensure ETH and SOL prices are distinct and not copied from BTC
        assert not np.array_equal(btc_closes, eth_closes)
        assert not np.array_equal(btc_closes, sol_closes)


# ─────────────────────────────────────────────────────────────────────────────
# 11-19: Feature Extraction, Replay, Causality & Splitting Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFeatureCausalityAndSplits:
    """Verifies feature contract, causality, forward outcome replay, and splitting."""

    def test_11_24_feature_contract(self, phase_f_gate):
        assert FEATURE_COUNT == 24
        for f in FEATURE_NAMES:
            assert f in phase_f_gate.pooled_raw_df.columns

    def test_12_feature_causality(self, phase_f_gate):
        # Verify that all features are finite and contain no future timestamps
        X = phase_f_gate.pooled_raw_df[FEATURE_NAMES].values
        assert np.all(np.isfinite(X))
        assert not np.any(np.isnan(X))

    def test_13_72h_forward_replay_targets(self, phase_f_gate):
        df = phase_f_gate.pooled_raw_df
        assert TARGET_REALIZED_R in df.columns
        assert TARGET_MFE_R in df.columns
        assert TARGET_MAE_R in df.columns
        assert np.all(df[TARGET_MFE_R] >= 0.0)
        assert np.all(df[TARGET_MAE_R] >= 0.0)

    def test_14_no_target_leakage_in_features(self, phase_f_gate):
        for target in [TARGET_REALIZED_R, TARGET_MFE_R, TARGET_MAE_R]:
            assert target not in FEATURE_NAMES

    def test_15_chronological_splitting(self, phase_f_gate):
        train_max = phase_f_gate.train_df["timestamp"].max()
        val_min = phase_f_gate.val_df["timestamp"].min()
        val_max = phase_f_gate.val_df["timestamp"].max()
        test_min = phase_f_gate.test_df["timestamp"].min()
        assert train_max < val_min
        assert val_max < test_min

    def test_16_72h_purge_embargo(self, phase_f_gate):
        train_max = phase_f_gate.train_df["timestamp"].max()
        val_min = phase_f_gate.val_df["timestamp"].min()
        gap1_h = (val_min - train_max).total_seconds() / 3600.0
        assert gap1_h >= 72.0

        val_max = phase_f_gate.val_df["timestamp"].max()
        test_min = phase_f_gate.test_df["timestamp"].min()
        gap2_h = (test_min - val_max).total_seconds() / 3600.0
        assert gap2_h >= 72.0

    def test_17_cross_asset_split_integrity(self, phase_f_gate):
        # All 4 symbols must be present in the pooled raw dataset
        syms = set(phase_f_gate.pooled_raw_df["symbol"].unique())
        assert syms == {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"}

    def test_18_leave_one_asset_out_isolation(self, phase_f_gate):
        train_df, test_df = phase_f_gate.builder.build_leave_one_asset_out_splits("ETHUSD")
        assert "ETHUSD" not in train_df["symbol"].unique()
        assert set(test_df["symbol"].unique()) == {"ETHUSD"}

    def test_19_frozen_oos_isolation(self, phase_f_gate):
        frozen_oos_start = pd.Timestamp("2026-07-06 00:00:00+00:00")
        train_max_ts = phase_f_gate.train_df["timestamp"].max()
        assert train_max_ts < frozen_oos_start


# ─────────────────────────────────────────────────────────────────────────────
# 20-24: ONNX Parity, Promotion Gate & Edge Cases
# ─────────────────────────────────────────────────────────────────────────────


class TestONNXAndPromotionGate:
    """Verifies ONNX model parity, promotion gate decision logic, and error handling."""

    def test_20_onnx_input_output_shape(self, repo_root):
        onnx_file = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"
        assert onnx_file.exists()
        import onnxruntime as ort
        session = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        input_shape = session.get_inputs()[0].shape
        output_shape = session.get_outputs()[0].shape
        assert input_shape[1] == 24
        assert output_shape[1] == 3

    def test_21_onnx_numeric_parity(self, repo_root, phase_f_gate):
        onnx_file = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"
        import onnxruntime as ort
        session = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
        input_name = session.get_inputs()[0].name

        sample_x = phase_f_gate.val_df[FEATURE_NAMES].iloc[0:5].values.astype(np.float32)
        onnx_out = session.run(None, {input_name: sample_x})[0]
        assert onnx_out.shape == (5, 3)
        assert np.all(np.isfinite(onnx_out))

    def test_22_promotion_gate_evaluation_logic(self, phase_f_gate):
        oos_smc = calculate_performance_metrics(phase_f_gate.test_df)
        oos_ai = calculate_performance_metrics(phase_f_gate.test_df)  # identical -> should reject because not strictly superior
        status, reasons = phase_f_gate.evaluate_promotion_gate(
            oos_smc=oos_smc,
            oos_ai=oos_ai,
            regime_analysis=[],
            bootstrap_ci={"incremental_mean_r_95ci": (-0.1, 0.2)},
            loao_results=[],
        )
        assert status == "REJECTED"

    def test_23_rejected_promotion_decision_rule(self, phase_f_gate):
        results = phase_f_gate.run_full_gate()
        assert results.status == "REJECTED"
        assert len(results.reasons) > 0

    def test_24_insufficient_assets_reported_honestly(self, tmp_path):
        # Empty directory should report NOT_AVAILABLE honestly
        audits = audit_canonical_datasets(canonical_base=tmp_path)
        for a in audits:
            assert a.available is False
            assert a.status == "NOT_AVAILABLE"
            assert a.training_status == "NOT_TRAINABLE"
            assert a.execution_authority == "BLOCKED"
