"""
QuantEdge AI — Real-Market Model Training Pipeline.

Trains a multi-output regressor on genuine historical market setups discovered
by the deterministic SMC engine and evaluated through forward candle replay.
Exports the trained model to ONNX for low-latency Java inference.

═══════════════════════════════════════════════════════════════════════════════
PIPELINE ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════════
1. Real Data Ingestion & Causal Replay across Canonical Assets (multi_asset_dataset_builder)
2. Purged Chronological 3-Way Split (60% Train / 20% Val / 20% Final OOS Test)
   Enforces a >= 72-hour embargo window to eliminate forward-horizon contamination.
3. Data Hygiene & Leakage Audit (leakage_detector)
4. Multi-Output Random Forest Training using Authoritative MODEL_CONFIG
5. Out-of-Sample Evaluation on Validation and Final Test Splits
6. ONNX Model Export (Target opset 15)
7. Sklearn <-> ONNX Runtime Numeric Parity Verification Gate (< 1e-3)
8. Deploy to Spring Boot Classpath (backend/src/main/resources/models/quantedge-ai-v2.onnx)
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.dataset_builder import build_training_dataset
from quantedge.ai.training.leakage_detector import (
    DataHygieneReport,
    run_all_checks,
    run_all_purged_checks,
    split_purged_chronological,
)
from quantedge.ai.training.model_config import (
    AUTHORITATIVE_MODEL_CONFIG,
    compute_dataset_fingerprint,
    compute_onnx_sha256,
)
from quantedge.ai.training.multi_asset_dataset_builder import MultiAssetDatasetBuilder
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
)


def _get_default_onnx_path() -> Path:
    cur = Path(__file__).resolve()
    for parent in cur.parents:
        cand = parent / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"
        if cand.parent.exists():
            return cand
    return cur.parents[4] / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"


_DEFAULT_ONNX_OUT = _get_default_onnx_path()


# ─────────────────────────────────────────────────────────────────────────────
# Stage Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _load_or_build_dataset(
    data_source: str,
    csv_path: Optional[Path],
    n_samples: int,
    seed: int,
) -> Tuple[pd.DataFrame, List[str]]:
    if data_source == "real":
        _print_section("Stage 1: Building Multi-Asset Dataset from Canonical Market Data")
        if csv_path is not None:
            print(f"  Source (Single Asset): {csv_path}")
            t0 = time.monotonic()
            df = build_real_training_dataset(csv_path=csv_path, verbose=True)
            elapsed = time.monotonic() - t0
            print(f"  Extraction + Replay finished in {elapsed:.1f}s -> {df.shape}")
        else:
            builder = MultiAssetDatasetBuilder()
            symbols = builder.get_available_symbols()
            print(f"  Source (Multi-Asset Canonical): {symbols}")
            t0 = time.monotonic()
            df = builder.build_pooled_dataset()
            elapsed = time.monotonic() - t0
            print(f"  Multi-Asset Extraction finished in {elapsed:.1f}s -> {df.shape}")
        target_names = list(AUTHORITATIVE_MODEL_CONFIG.target_columns)
    else:
        _print_section("Stage 1: Generating Synthetic Prototype Dataset [UNIT-TEST FIXTURE ONLY]")
        print("  [WARNING] Synthetic datasets are strictly for infrastructure testing and must NEVER be promoted to live execution.")
        print(f"  n_samples={n_samples:,}  seed={seed}")
        t0 = time.monotonic()
        df = build_training_dataset(n_samples=n_samples, seed=seed)
        elapsed = time.monotonic() - t0
        print(f"  Generated in {elapsed:.1f}s -> {df.shape}")
        target_names = ["target_pattern_score", "target_signal_score", "target_confidence"]

    return df, target_names


def _train_sklearn_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    config: Optional[Any] = None,
) -> Any:
    """Trains a MultiOutputRegressor wrapping RandomForestRegressor with authoritative parameters."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.multioutput import MultiOutputRegressor

    cfg = config or AUTHORITATIVE_MODEL_CONFIG
    _print_section(f"Stage 4: Training Multi-Output Random Forest ({cfg.model_name})")

    base = RandomForestRegressor(
        n_estimators=cfg.n_estimators,
        max_depth=cfg.max_depth,
        min_samples_leaf=cfg.min_samples_leaf,
        max_features=cfg.max_features,
        random_state=cfg.random_state,
        n_jobs=cfg.n_jobs,
    )
    model = MultiOutputRegressor(base, n_jobs=1)

    t0 = time.monotonic()
    model.fit(X_train, y_train)
    elapsed = time.monotonic() - t0
    print(f"  Training complete in {elapsed:.1f}s")
    print(
        f"  Hyperparameters: n_estimators={cfg.n_estimators} max_depth={cfg.max_depth} "
        f"min_samples_leaf={cfg.min_samples_leaf} max_features={cfg.max_features} random_state={cfg.random_state}"
    )
    return model


def _evaluate_split(
    model: Any,
    X: np.ndarray,
    y: np.ndarray,
    target_names: List[str],
    split_name: str,
) -> Dict[str, Any]:
    """Computes evaluation metrics (MAE, MSE, R2, Directional Accuracy)."""
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    print(f"\n  [{split_name} Evaluation — {len(X)} samples]")
    if len(X) == 0:
        return {}
    y_pred = model.predict(X)
    metrics: Dict[str, Any] = {}

    for i, name in enumerate(target_names):
        mae = mean_absolute_error(y[:, i], y_pred[:, i])
        mse = mean_squared_error(y[:, i], y_pred[:, i])
        r2 = r2_score(y[:, i], y_pred[:, i]) if len(y) > 1 else 0.0
        metrics[name] = {"MAE": round(float(mae), 4), "MSE": round(float(mse), 4), "R2": round(float(r2), 4)}
        short = name.replace("target_", "")
        print(f"    {short:<18}  MAE={mae:.4f}  MSE={mse:.4f}  R²={r2:.4f}")

    if TARGET_REALIZED_R in target_names:
        r_idx = target_names.index(TARGET_REALIZED_R)
        actual_pos = y[:, r_idx] > 0
        pred_pos = y_pred[:, r_idx] > 0
        accuracy = np.mean(actual_pos == pred_pos)
        metrics["directional_win_accuracy"] = round(float(accuracy), 4)
        print(f"    Directional Accuracy (Win/Loss Classification): {accuracy * 100:.1f}%")

    return metrics


def _export_onnx(model: Any, output_path: Path, n_targets: int) -> str:
    """Converts the sklearn model to ONNX float32 format and returns its SHA-256."""
    try:
        from skl2onnx import to_onnx
        from skl2onnx.common.data_types import FloatTensorType
    except ImportError:
        raise RuntimeError("skl2onnx not installed. Run: pip install skl2onnx")

    _print_section("Stage 6: Exporting ONNX Model")

    initial_types = [("float_input", FloatTensorType([None, FEATURE_COUNT]))]
    try:
        onnx_model = to_onnx(
            model,
            initial_types=initial_types,
            target_opset=15,
        )
    except MemoryError as e:
        raise RuntimeError(f"ONNX export out of memory: {e}") from e

    output_path.parent.mkdir(parents=True, exist_ok=True)
    model_bytes = onnx_model.SerializeToString()
    output_path.write_bytes(model_bytes)

    sha256_hash = hashlib.sha256(model_bytes).hexdigest()
    size_kb = len(model_bytes) / 1024.0
    print(f"  Written: {output_path}")
    print(f"  Size:    {size_kb:.1f} KB")
    print(f"  SHA-256: {sha256_hash}")

    return sha256_hash


def _smoke_test_onnx(
    model: Any,
    onnx_path: Path,
    X_sample: np.ndarray,
    target_names: List[str],
) -> None:
    """Verifies ONNX output matches sklearn output to within 1e-3."""
    try:
        import onnxruntime as rt
    except ImportError:
        print("  [WARN] onnxruntime not installed — skipping ONNX smoke test.")
        return

    _print_section("Stage 7: ONNX Smoke Test (sklearn <-> ONNX numeric parity)")

    sess = rt.InferenceSession(str(onnx_path))
    input_name = sess.get_inputs()[0].name

    X_f32 = X_sample.astype(np.float32)
    onnx_pred = sess.run(None, {input_name: X_f32})

    if isinstance(onnx_pred, list) and len(onnx_pred) >= len(target_names):
        onnx_matrix = np.column_stack([onnx_pred[i] for i in range(len(target_names))])
    elif isinstance(onnx_pred, list) and len(onnx_pred) == 1:
        onnx_matrix = onnx_pred[0]
    else:
        onnx_matrix = np.array(onnx_pred)

    sklearn_pred = model.predict(X_sample)
    if onnx_matrix.shape != sklearn_pred.shape:
        onnx_matrix = onnx_matrix.reshape(sklearn_pred.shape)

    max_diff = float(np.max(np.abs(onnx_matrix - sklearn_pred)))
    print(f"  Max abs diff sklearn vs ONNX: {max_diff:.2e}")

    TOLERANCE = 1e-3
    if max_diff > TOLERANCE:
        raise RuntimeError(
            f"ONNX smoke test FAILED: max abs diff {max_diff:.2e} > tolerance {TOLERANCE}."
        )
    else:
        print(f"  [OK] ONNX parity confirmed (max diff {max_diff:.2e} < {TOLERANCE})")


# ─────────────────────────────────────────────────────────────────────────────
# Main Pipeline Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def run_pipeline(
    data_source: str = "real",
    csv_path: Optional[Path] = None,
    dataset_path: Optional[Path] = None,
    n_samples: int = 50_000,
    seed: int = 42,
    train_ratio: float = 0.60,
    train_split: Optional[float] = None,
    val_ratio: float = 0.20,
    test_ratio: float = 0.20,
    embargo_hours: float = 72.0,
    onnx_output: Optional[Path] = None,
    skip_hygiene: bool = False,
) -> Dict[str, Any]:
    """
    Runs the complete real-market training pipeline.
    """
    if dataset_path is not None and csv_path is None:
        csv_path = dataset_path
    if train_split is not None:
        train_ratio = train_split

    if onnx_output is None:
        onnx_output = _DEFAULT_ONNX_OUT

    print("\n")
    print("+" + "-" * 58 + "+")
    print("|  QuantEdge AI v2 -- Real-Market Training Pipeline       |")
    print("+" + "-" * 58 + "+")

    # ── Stage 1: Dataset ──────────────────────────────────────────────────────
    df, target_names = _load_or_build_dataset(data_source, csv_path, n_samples, seed)

    # ── Stage 2: 3-Way Purged Chronological Split ─────────────────────────────
    _print_section("Stage 2: Purged Chronological 3-Way Splitting (72h Embargo)")
    if data_source == "real":
        train_df, val_df, test_df = split_purged_chronological(
            df,
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            test_ratio=test_ratio,
            embargo_hours=embargo_hours,
        )
    else:
        train_end = int(len(df) * 0.80)
        train_df = df.iloc[:train_end].copy()
        val_df = df.iloc[train_end:].copy()
        test_df = val_df.copy()

    print(f"  Train: {len(train_df):,} setups ({train_df['timestamp'].min()} to {train_df['timestamp'].max()})")
    print(f"  Val:   {len(val_df):,} setups ({val_df['timestamp'].min()} to {val_df['timestamp'].max()})")
    print(f"  Test:  {len(test_df):,} setups ({test_df['timestamp'].min()} to {test_df['timestamp'].max()})")

    # ── Stage 3: Data Hygiene Audit ───────────────────────────────────────────
    if not skip_hygiene:
        _print_section("Stage 3: Data Hygiene & Purge Embargo Audit")
        if data_source == "real":
            report = run_all_purged_checks(train_df, val_df, test_df, embargo_hours=embargo_hours, verbose=True)
        else:
            report = run_all_checks(df, verbose=True)
        if not report.passed:
            raise RuntimeError(f"Training aborted — data hygiene failed:\n{report.summary}")
        print("  [OK] All data-hygiene and embargo checks passed.")
    else:
        print("\n  [Stage 3 SKIPPED — skip_hygiene=True]")

    # ── Stage 4: Feature / Target Arrays ──────────────────────────────────────
    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[target_names].values.astype(np.float32)
    X_val = val_df[FEATURE_NAMES].values.astype(np.float32)
    y_val = val_df[target_names].values.astype(np.float32)
    X_test = test_df[FEATURE_NAMES].values.astype(np.float32)
    y_test = test_df[target_names].values.astype(np.float32)

    # ── Stage 5: Train Model ──────────────────────────────────────────────────
    model = _train_sklearn_model(X_train, y_train, AUTHORITATIVE_MODEL_CONFIG)

    # ── Stage 6: Evaluate on Val and Final Test Splits ────────────────────────
    _print_section("Stage 5: Validation & Final Out-of-Sample Evaluation")
    val_metrics = _evaluate_split(model, X_val, y_val, target_names, "Validation Split")
    test_metrics = _evaluate_split(model, X_test, y_test, target_names, "Final Out-of-Sample Test Split")

    # ── Stage 7: Export ONNX ──────────────────────────────────────────────────
    onnx_sha = _export_onnx(model, onnx_output, len(target_names))

    # ── Stage 8: Smoke Test ───────────────────────────────────────────────────
    smoke_sample = X_test[: min(50, len(X_test))]
    _smoke_test_onnx(model, onnx_output, smoke_sample, target_names)

    fingerprint = compute_dataset_fingerprint()

    _print_section("Pipeline Complete [OK]")
    print(f"  Model written to:    {onnx_output}")
    print(f"  ONNX SHA-256:        {onnx_sha}")
    print(f"  Dataset Fingerprint: {fingerprint}")
    print("  [STATUS] Technical training & ONNX export complete.")
    print("  [GOVERNANCE] Live execution authority requires passing the AI Predictive-Value Promotion Gate.")
    print("               Run 'python -m quantedge.ai.evaluation.run_phase_f' to evaluate production promotion eligibility.")
    print()

    return {
        "data_source": data_source,
        "n_train": len(train_df),
        "n_val": len(val_df),
        "n_test": len(test_df),
        "validation_metrics": val_metrics,
        "test_metrics": test_metrics,
        "onnx_path": str(onnx_output),
        "onnx_sha256": onnx_sha,
        "dataset_fingerprint": fingerprint,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="QuantEdge AI v2 — Real-Market Training Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data-source", choices=["real", "synthetic"], default="real", help="Data source")
    parser.add_argument("--csv-path", type=Path, default=None, help="Path to historical CSV (for 'real')")
    parser.add_argument("--n-samples", type=int, default=50_000, help="Number of samples (for 'synthetic')")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--embargo-hours", type=float, default=72.0, help="Purge/embargo window (hours)")
    parser.add_argument("--onnx-output", type=Path, default=None, help="Path to write .onnx file")
    parser.add_argument("--skip-hygiene", action="store_true", default=False, help="Skip data-hygiene checks")
    args = parser.parse_args()

    try:
        result = run_pipeline(
            data_source=args.data_source,
            csv_path=args.csv_path,
            n_samples=args.n_samples,
            seed=args.seed,
            embargo_hours=args.embargo_hours,
            onnx_output=args.onnx_output,
            skip_hygiene=args.skip_hygiene,
        )
        print(f"Result summary: {result}")
        sys.exit(0)
    except RuntimeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
