"""
QuantEdge AI — Full Training Pipeline.

Trains a Random Forest regressor on the synthetic SMC dataset and exports it
as a multi-output ONNX model that the Java OnnxModelInferenceService loads at startup.

═══════════════════════════════════════════════════════════════════════════════
PIPELINE STAGES
═══════════════════════════════════════════════════════════════════════════════
1. Build / load dataset                         (dataset_builder)
2. Run data hygiene checks                      (leakage_detector)  ← MANDATORY
3. Temporal split (train 80 / val 20)
4. Train multi-output Random Forest
5. Evaluate on validation set (MAE, R², per-output)
6. Export ONNX model                            (skl2onnx)
7. Smoke-test ONNX output vs sklearn output     (numeric parity gate)
8. Write model to resources path for Java

═══════════════════════════════════════════════════════════════════════════════
REQUIREMENTS
═══════════════════════════════════════════════════════════════════════════════
    pip install scikit-learn skl2onnx onnxruntime numpy pandas

═══════════════════════════════════════════════════════════════════════════════
USAGE
═══════════════════════════════════════════════════════════════════════════════
    python -m quantedge.ai.training.train

Or programmatically:

    from quantedge.ai.training.train import run_pipeline
    run_pipeline(n_samples=50_000, seed=42)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.dataset_builder import build_training_dataset, describe_dataset
from quantedge.ai.training.leakage_detector import run_all_checks

# Target column names — order matters for ONNX output interpretation
TARGET_NAMES = ["target_pattern_score", "target_signal_score", "target_confidence"]

# Default output path — Java backend classpath resource
_DEFAULT_ONNX_OUT = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"
)


# =============================================================================
# Stage helpers
# =============================================================================

def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _load_or_build_dataset(
    dataset_path: Optional[Path],
    n_samples: int,
    seed: int,
) -> pd.DataFrame:
    if dataset_path and dataset_path.exists():
        _print_section("Stage 1: Loading existing dataset")
        print(f"  Reading: {dataset_path}")
        if dataset_path.suffix == ".parquet":
            df = pd.read_parquet(dataset_path)
        else:
            df = pd.read_csv(dataset_path, parse_dates=["timestamp"])
        print(f"  Loaded {len(df):,} rows × {len(df.columns)} columns")
    else:
        _print_section("Stage 1: Generating synthetic training dataset")
        print(f"  n_samples={n_samples:,}  seed={seed}")
        t0 = time.monotonic()
        df = build_training_dataset(n_samples=n_samples, seed=seed)
        elapsed = time.monotonic() - t0
        print(f"  Generated in {elapsed:.1f}s  →  {df.shape}")

        if dataset_path:
            dataset_path.parent.mkdir(parents=True, exist_ok=True)
            if dataset_path.suffix == ".parquet":
                df.to_parquet(dataset_path, index=False)
            else:
                df.to_csv(dataset_path, index=False)
            print(f"  Saved to: {dataset_path}")

    return df


def _train_sklearn_model(X_train: np.ndarray, y_train: np.ndarray) -> object:
    """Trains a MultiOutputRegressor wrapping RandomForestRegressor."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.multioutput import MultiOutputRegressor

    _print_section("Stage 4: Training Random Forest")

    base = RandomForestRegressor(
        n_estimators=100,   # 200 causes MemoryError in skl2onnx string serialization
        max_depth=8,        # depth-12 trees produce ONNX graphs too large to serialize
        min_samples_leaf=5,
        max_features=0.6,
        random_state=42,
        n_jobs=-1,
    )
    model = MultiOutputRegressor(base, n_jobs=1)

    t0 = time.monotonic()
    model.fit(X_train, y_train)
    elapsed = time.monotonic() - t0
    print(f"  Training complete in {elapsed:.1f}s")
    print(f"  n_estimators=100  max_depth=8  n_features={X_train.shape[1]}")
    return model



def _evaluate_model(model: object, X_val: np.ndarray, y_val: np.ndarray) -> dict:
    """Computes MAE and R² per output on the validation set."""
    from sklearn.metrics import mean_absolute_error, r2_score

    _print_section("Stage 5: Validation Set Evaluation")

    y_pred = model.predict(X_val)
    metrics = {}
    for i, name in enumerate(TARGET_NAMES):
        mae = mean_absolute_error(y_val[:, i], y_pred[:, i])
        r2 = r2_score(y_val[:, i], y_pred[:, i])
        metrics[name] = {"MAE": round(mae, 4), "R2": round(r2, 4)}
        short = name.replace("target_", "")
        print(f"  {short:<20}  MAE={mae:.4f}  R²={r2:.4f}")

    # Warn if any output is poorly fitted
    for name, m in metrics.items():
        if m["R2"] < 0.50:
            print(
                f"  ⚠  WARNING: {name} R²={m['R2']:.3f} < 0.50. "
                "Model may need more data or feature engineering."
            )

    return metrics


def _export_onnx(
    model: object,
    output_path: Path,
) -> None:
    """Converts the sklearn model to ONNX float32 format."""
    try:
        from skl2onnx import to_onnx
        from skl2onnx.common.data_types import FloatTensorType
    except ImportError:
        raise RuntimeError(
            "skl2onnx not installed. Run: pip install skl2onnx"
        )

    _print_section("Stage 6: Exporting ONNX Model")

    initial_types = [("float_input", FloatTensorType([None, FEATURE_COUNT]))]
    # MultiOutputRegressor needs explicit output type specification
    output_types = [("variable", FloatTensorType([None, len(TARGET_NAMES)]))]
    try:
        onnx_model = to_onnx(
            model,
            initial_types=initial_types,
            target_opset=15,  # opset 15 has broad skl2onnx support; 17 can OOM on large RFs
        )
    except MemoryError as e:
        raise RuntimeError(
            "ONNX export ran out of memory. Reduce n_estimators or max_depth. "
            f"Original error: {e}"
        ) from e


    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(onnx_model.SerializeToString())

    size_kb = output_path.stat().st_size / 1024
    print(f"  Written: {output_path}")
    print(f"  Size:    {size_kb:.1f} KB")


def _smoke_test_onnx(
    model: object,
    onnx_path: Path,
    X_sample: np.ndarray,
) -> None:
    """Verifies ONNX output matches sklearn output to within 1e-4."""
    try:
        import onnxruntime as rt
    except ImportError:
        print("  ⚠  onnxruntime not installed — skipping ONNX smoke test.")
        return

    _print_section("Stage 7: ONNX Smoke Test (sklearn ↔ ONNX numeric parity)")

    sess = rt.InferenceSession(str(onnx_path))
    input_name = sess.get_inputs()[0].name

    X_f32 = X_sample.astype(np.float32)
    onnx_pred = sess.run(None, {input_name: X_f32})

    # MultiOutputRegressor ONNX output is a list of per-output arrays
    # skl2onnx packs them as variable_output0, variable_output1, ...
    if isinstance(onnx_pred, list) and len(onnx_pred) >= len(TARGET_NAMES):
        onnx_matrix = np.column_stack([onnx_pred[i] for i in range(len(TARGET_NAMES))])
    elif isinstance(onnx_pred, list) and len(onnx_pred) == 1:
        onnx_matrix = onnx_pred[0]
    else:
        onnx_matrix = np.array(onnx_pred)

    sklearn_pred = model.predict(X_sample)
    # Align shapes
    if onnx_matrix.shape != sklearn_pred.shape:
        onnx_matrix = onnx_matrix.reshape(sklearn_pred.shape)

    max_diff = np.max(np.abs(onnx_matrix - sklearn_pred))
    print(f"  Max abs diff sklearn vs ONNX: {max_diff:.2e}")

    TOLERANCE = 1e-3
    if max_diff > TOLERANCE:
        raise RuntimeError(
            f"ONNX smoke test FAILED: max abs diff {max_diff:.2e} > tolerance {TOLERANCE}. "
            "The exported ONNX model does not match the sklearn model. Do not deploy."
        )
    else:
        print(f"  ✓ ONNX parity confirmed (max diff {max_diff:.2e} < {TOLERANCE})")


# =============================================================================
# Main pipeline entry point
# =============================================================================

def run_pipeline(
    n_samples: int = 50_000,
    seed: int = 42,
    train_split: float = 0.80,
    onnx_output: Optional[Path] = None,
    dataset_path: Optional[Path] = None,
    skip_hygiene: bool = False,
) -> dict:
    """
    Runs the complete training pipeline.

    Args:
        n_samples: Number of training samples to generate if no dataset_path provided.
        seed: RNG seed.
        train_split: Fraction of data to use for training (rest = validation).
        onnx_output: Path to write the .onnx file. Defaults to Java resources.
        dataset_path: Optional path to load/save dataset (.parquet or .csv).
        skip_hygiene: Set to True only for unit tests. Never set in production.

    Returns:
        Dictionary of pipeline results including validation metrics.

    Raises:
        RuntimeError if data hygiene checks fail (unless skip_hygiene=True).
    """
    if onnx_output is None:
        onnx_output = _DEFAULT_ONNX_OUT

    print("\n")
    print("+" + "-" * 58 + "+")
    print("|  QuantEdge AI v2 -- Training Pipeline                   |")
    print("+" + "-" * 58 + "+")

    # -- Stage 1: Dataset ------------------------------------------------------
    df = _load_or_build_dataset(dataset_path, n_samples, seed)

    # -- Stage 2: Data hygiene -------------------------------------------------
    if not skip_hygiene:
        _print_section("Stage 2: Data Hygiene Checks")
        train_end_idx = int(len(df) * train_split)
        report = run_all_checks(df, train_end_idx=train_end_idx, verbose=True)
        if not report.passed:
            raise RuntimeError(
                f"Training aborted — data hygiene checks failed:\n{report.summary}"
            )
        print("  ✓ All data-hygiene checks passed.")
    else:
        print("\n  [Stage 2 SKIPPED — skip_hygiene=True]")
        train_end_idx = int(len(df) * train_split)

    # ── Stage 3: Temporal split ───────────────────────────────────────────────
    _print_section("Stage 3: Temporal Train / Val Split")
    train_df = df.iloc[:train_end_idx].copy()
    val_df = df.iloc[train_end_idx:].copy()

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df[TARGET_NAMES].values.astype(np.float32)
    X_val = val_df[FEATURE_NAMES].values.astype(np.float32)
    y_val = val_df[TARGET_NAMES].values.astype(np.float32)

    print(f"  Train: {len(train_df):,} rows  ({100 * train_split:.0f}%)")
    print(f"  Val:   {len(val_df):,} rows  ({100 * (1 - train_split):.0f}%)")
    print(f"  X shape: {X_train.shape}  y shape: {y_train.shape}")

    # ── Stage 4: Train ────────────────────────────────────────────────────────
    model = _train_sklearn_model(X_train, y_train)

    # ── Stage 5: Evaluate ─────────────────────────────────────────────────────
    metrics = _evaluate_model(model, X_val, y_val)

    # ── Stage 6: Export ONNX ──────────────────────────────────────────────────
    _export_onnx(model, onnx_output)

    # ── Stage 7: Smoke test ───────────────────────────────────────────────────
    smoke_sample = X_val[:50]
    _smoke_test_onnx(model, onnx_output, smoke_sample)

    _print_section("Pipeline Complete ✓")
    print(f"  ONNX model written to: {onnx_output}")
    print(
        "  Deploy by restarting the Spring Boot backend. "
        "OnnxModelInferenceService will load the model on startup."
    )
    print()

    return {
        "n_train": len(train_df),
        "n_val": len(val_df),
        "validation_metrics": metrics,
        "onnx_path": str(onnx_output),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QuantEdge AI v2 — Training Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--n-samples", type=int, default=50_000, help="Number of training samples")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--train-split", type=float, default=0.80, help="Train fraction [0.5, 0.95]")
    parser.add_argument("--onnx-output", type=Path, default=None, help="Path to write .onnx file")
    parser.add_argument("--dataset-path", type=Path, default=None, help="Load/save dataset (.parquet)")
    parser.add_argument("--skip-hygiene", action="store_true", default=False,
                        help="Skip data-hygiene checks (only for debugging)")
    args = parser.parse_args()

    if args.skip_hygiene:
        print("⚠  WARNING: --skip-hygiene is set. Data-hygiene checks are DISABLED.")
        print("   Never use this flag in a production training run.")

    try:
        result = run_pipeline(
            n_samples=args.n_samples,
            seed=args.seed,
            train_split=args.train_split,
            onnx_output=args.onnx_output,
            dataset_path=args.dataset_path,
            skip_hygiene=args.skip_hygiene,
        )
        print(f"Result: {result}")
        sys.exit(0)
    except RuntimeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
