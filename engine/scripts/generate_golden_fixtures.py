"""
Generate Phase G Golden Vector Fixtures from canonical Delta Exchange India datasets.
Covers BTCUSD, ETHUSD, SOLUSD, XRPUSD across diverse timestamps, long/short setups,
and calculates expected ONNX model outputs.
"""

import json
from pathlib import Path
import numpy as np
import onnxruntime as ort
from quantedge.ai.training.real_dataset_builder import build_real_training_dataset
from quantedge.ai.feature_contract import FEATURE_NAMES

def generate_golden_fixtures():
    repo_root = Path(__file__).resolve().parents[2]
    canonical_dir = repo_root / "data" / "canonical" / "delta_exchange_india"
    symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    onnx_path = repo_root / "backend" / "src" / "main" / "resources" / "models" / "quantedge-ai-v2.onnx"

    session = ort.InferenceSession(str(onnx_path))
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name

    golden_cases = []

    for sym in symbols:
        csv_path = canonical_dir / sym / "1h" / "2026.csv"
        df = build_real_training_dataset(csv_path=csv_path, symbol=f"{sym}.P", verbose=False)
        print(f"Symbol {sym}: {len(df)} setups extracted")

        long_setups = df[df["meta_direction"] == "LONG"]
        short_setups = df[df["meta_direction"] == "SHORT"]

        selected_indices = []
        if len(long_setups) >= 3:
            indices = np.linspace(0, len(long_setups) - 1, 3, dtype=int)
            selected_indices.extend(long_setups.iloc[indices].index.tolist())
        else:
            selected_indices.extend(long_setups.index.tolist())

        if len(short_setups) >= 3:
            indices = np.linspace(0, len(short_setups) - 1, 3, dtype=int)
            selected_indices.extend(short_setups.iloc[indices].index.tolist())
        else:
            selected_indices.extend(short_setups.index.tolist())

        for idx in selected_indices:
            row = df.loc[idx]
            feat_vec = [float(row[col]) for col in FEATURE_NAMES]

            inp = np.array([feat_vec], dtype=np.float32)
            onnx_out = session.run([output_name], {input_name: inp})[0][0].tolist()

            case = {
                "case_id": f"{sym}_{row['meta_setup_id']}",
                "symbol": sym,
                "timestamp": str(row["timestamp"]),
                "direction": str(row["meta_direction"]),
                "features_24": feat_vec,
                "expected_onnx_output": {
                    "predicted_realized_r": float(onnx_out[0]),
                    "predicted_mfe_r": float(onnx_out[1]),
                    "predicted_mae_r": float(onnx_out[2])
                }
            }
            golden_cases.append(case)

    print(f"Total golden cases generated: {len(golden_cases)}")

    fixture_dir = repo_root / "engine" / "tests" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / "phase_g_golden_vectors.json"

    fixture_data = {
        "schema_version": "1.0",
        "phase": "Phase G",
        "total_cases": len(golden_cases),
        "cases": golden_cases
    }

    with open(fixture_path, "w", encoding="utf-8") as f:
        json.dump(fixture_data, f, indent=2)

    # Copy to backend test resources
    backend_fixture_dir = repo_root / "backend" / "src" / "test" / "resources" / "fixtures"
    backend_fixture_dir.mkdir(parents=True, exist_ok=True)
    with open(backend_fixture_dir / "phase_g_golden_vectors.json", "w", encoding="utf-8") as f:
        json.dump(fixture_data, f, indent=2)

    print("Fixture written successfully to both Python and Java test resources.")

if __name__ == "__main__":
    generate_golden_fixtures()
