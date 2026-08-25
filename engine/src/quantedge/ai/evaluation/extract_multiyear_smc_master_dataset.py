"""
QuantEdge AI — Multi-Year SMC Order Block Master Dataset Extractor (2024–2026).

Extracts every Order Block qualified by the authoritative production SMC engine
across the complete canonical history (June 2024 through August 2026) for BTCUSD,
ETHUSD, SOLUSD, and XRPUSD.

Outputs:
- docs/ai/multiyear_smc_order_blocks_master.csv
- docs/ai/multiyear_smc_order_blocks_master.json
- docs/ai/MULTI_YEAR_SMC_OB_MASTER_DATASET.md
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from quantedge.ai.evaluation.phase_i_ob_replay import (
    PHASE_I_TP_RR_CONFIG,
    REPLAY_HORIZON_BARS,
    WARMUP_BARS,
    SMCContext,
    build_smc_context,
    extract_phase_i_setups,
)
from quantedge.ai.evaluation.phase_j_ob_dataset import (
    FEATURE_DIM,
    OB_FEATURE_NAMES,
    extract_ob_causal_features,
)
from quantedge.ai.evaluation.phase_l_research import (
    SYMBOLS,
    _find_repo_root,
    load_canonical_full_history,
)
from quantedge.ai.training.real_dataset_builder import (
    TradeReplayOutcome,
    replay_forward_outcome,
)
from quantedge.strategy.models import StrategyDecision, StrategyDirection


def extract_multiyear_smc_master_dataset(
    canonical_base: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Extracts all qualified 2024–2026 Order Blocks with causal features and forward outcomes.
    """
    root = repo_root or _find_repo_root()
    base = canonical_base or (root / "data" / "canonical" / "delta_exchange_india")

    all_records: List[Dict[str, Any]] = []
    symbol_summaries: Dict[str, Any] = {}

    for sym in SYMBOLS:
        candles = load_canonical_full_history(base, sym)
        ctx = build_smc_context(candles)
        setups, audit = extract_phase_i_setups(candles, symbol=sym, ctx=ctx)

        print(f"[Multi-Year Extractor] {sym}: {len(setups)} qualified setups across {len(candles)} candles.")

        sym_records = 0
        for s in setups:
            dec_idx = s.decision_bar
            dec_dt = datetime.fromisoformat(s.decision_time)
            if dec_dt.tzinfo is None:
                dec_dt = dec_dt.replace(tzinfo=timezone.utc)

            decision = StrategyDecision(
                timestamp=datetime.fromisoformat(s.creation_time),
                symbol=s.asset,
                timeframe=s.timeframe,
                direction=StrategyDirection.LONG if s.direction == "LONG" else StrategyDirection.SHORT,
                setup_id=s.setup_id,
                entry=Decimal(str(s.entry_price)),
                stop_loss=Decimal(str(s.sl_price)),
                take_profit=Decimal(str(s.tp_price)),
                risk_distance=Decimal(str(s.risk_distance)),
            )

            outcome = replay_forward_outcome(
                setup_idx=dec_idx,
                candles=candles,
                decision=decision,
                max_holding_bars=REPLAY_HORIZON_BARS,
            )

            if outcome is None:
                continue

            feats = extract_ob_causal_features(s, candles, ctx)
            assert len(feats) == FEATURE_DIM

            exit_idx = dec_idx + outcome.holding_bars
            label_avail_ts = candles[exit_idx].timestamp.isoformat() if exit_idx < len(candles) else (dec_dt + timedelta(hours=outcome.holding_bars)).isoformat()

            rec: Dict[str, Any] = {
                "ob_id": s.setup_id,
                "asset": s.asset,
                "timeframe": s.timeframe,
                "direction": s.direction,
                "decision_timestamp": dec_dt.isoformat(),
                "creation_timestamp": s.creation_time,
                "confirmation_timestamp": s.confirmation_time,
                "formation_bar_index": s.formation_index,
                "break_bar_index": s.break_index,
                "decision_bar_index": dec_idx,
                "entry_price": s.entry_price,
                "sl_price": s.sl_price,
                "tp_price": s.tp_price,
                "risk_distance": s.risk_distance,
                "stop_distance_percent": s.stop_distance_percent,
                "leverage": s.leverage,
                "structural_event_id": s.structural_event_id,
                "structure_origin": s.structure_origin,
                "ob_high": s.ob_high,
                "ob_low": s.ob_low,
                "realized_r": round(float(outcome.realized_r), 6),
                "tp_first": 1 if outcome.exit_reason == "TP_HIT" else 0,
                "mfe_r": round(float(outcome.mfe_r), 6),
                "mae_r": round(float(outcome.mae_r), 6),
                "holding_bars": int(outcome.holding_bars),
                "exit_reason": outcome.exit_reason,
                "label_available_timestamp": label_avail_ts,
            }

            for name, val in zip(OB_FEATURE_NAMES, feats):
                rec[f"feat_{name}"] = round(float(val), 6)

            all_records.append(rec)
            sym_records += 1

        symbol_summaries[sym] = {
            "total_candles": len(candles),
            "qualified_obs": sym_records,
        }

    df = pd.DataFrame(all_records)
    # Sort chronologically by decision_timestamp, then asset
    df = df.sort_values(by=["decision_timestamp", "asset"]).reset_index(drop=True)

    metadata: Dict[str, Any] = {
        "dataset_name": "QuantEdge AI Multi-Year SMC Order Block Master Dataset",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_order_blocks": len(df),
        "feature_count": FEATURE_DIM,
        "features": list(OB_FEATURE_NAMES),
        "symbols": list(SYMBOLS),
        "symbol_breakdown": symbol_summaries,
        "earliest_decision_timestamp": df["decision_timestamp"].iloc[0],
        "latest_decision_timestamp": df["decision_timestamp"].iloc[-1],
    }

    return df, metadata


def write_multiyear_master_artifacts(
    df: pd.DataFrame,
    metadata: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Path]:
    """Writes the CSV, JSON, and Markdown documentation for the multi-year dataset."""
    root = repo_root or _find_repo_root()
    docs_dir = root / "docs" / "ai"
    docs_dir.mkdir(parents=True, exist_ok=True)

    csv_path = docs_dir / "multiyear_smc_order_blocks_master.csv"
    json_path = docs_dir / "multiyear_smc_order_blocks_master.json"
    doc_path = docs_dir / "MULTI_YEAR_SMC_OB_MASTER_DATASET.md"

    # 1. CSV
    df.to_csv(csv_path, index=False)

    # Compute SHA-256
    csv_bytes = csv_path.read_bytes()
    sha256_hash = hashlib.sha256(csv_bytes).hexdigest()
    metadata["csv_sha256"] = sha256_hash

    # 2. JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # 3. Markdown documentation
    _write_multiyear_dataset_doc(doc_path, df, metadata)

    return {
        "csv": csv_path,
        "json": json_path,
        "markdown": doc_path,
    }


def _write_multiyear_dataset_doc(
    doc_path: Path,
    df: pd.DataFrame,
    meta: Dict[str, Any],
) -> None:
    lines: List[str] = []
    lines.append("# QuantEdge AI — Multi-Year SMC Order Block Master Dataset (2024–2026)\n\n")
    lines.append(f"**Generated:** `{meta['generated_at_utc']}`  \n")
    lines.append(f"**Total Qualified Order Blocks:** `{meta['total_order_blocks']}`  \n")
    lines.append(f"**Date Span:** `{meta['earliest_decision_timestamp']}` to `{meta['latest_decision_timestamp']}`  \n")
    lines.append(f"**CSV SHA-256:** `{meta.get('csv_sha256', '')}`  \n\n---\n\n")

    lines.append("## 1. Asset & Year Distribution\n\n")
    df["dt"] = pd.to_datetime(df["decision_timestamp"], utc=True)
    df["year"] = df["dt"].dt.year
    cross = pd.crosstab(df["asset"], df["year"], margins=True)
    lines.append("| Asset | 2024 | 2025 | 2026 | All |\n")
    lines.append("|---|---:|---:|---:|---:|\n")
    for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "All"]:
        if sym in cross.index:
            r = cross.loc[sym]
            lines.append(f"| **{sym}** | {r.get(2024, 0)} | {r.get(2025, 0)} | {r.get(2026, 0)} | {r.get('All', 0)} |\n")
    lines.append("\n---\n\n")

    lines.append("## 2. Feature Schema (29 Scale-Invariant Causal Features)\n\n")
    lines.append("| # | Feature Column | Source Description | Causal Invariant |\n")
    lines.append("|---|---|---|:---:|\n")
    for idx, fname in enumerate(OB_FEATURE_NAMES, 1):
        lines.append(f"| {idx} | `feat_{fname}` | Computed at decision bar $T$ | Strictly $\\le T$ |\n")
    lines.append("\n---\n\n")

    lines.append("## 3. Label & Outcome Columns\n\n")
    lines.append("| Column | Type | Description |\n")
    lines.append("|---|---|---|\n")
    lines.append("| `realized_r` | float | Realized forward R-multiple (+1.7143R TP, -1.0000R SL, or timeout) |\n")
    lines.append("| `tp_first` | int | 1 if TP reached before SL/timeout, else 0 |\n")
    lines.append("| `mfe_r` | float | Maximum Favorable Excursion in R |\n")
    lines.append("| `mae_r` | float | Maximum Adverse Excursion in R |\n")
    lines.append("| `holding_bars` | int | Exit candle index minus decision bar index (1 to 72) |\n")
    lines.append("| `exit_reason` | string | `TP_HIT`, `SL_HIT`, or `TIMEOUT_EXIT` |\n")
    lines.append("| `label_available_timestamp` | string | Exact UTC timestamp when trade outcome was finalized |\n")

    with open(doc_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
