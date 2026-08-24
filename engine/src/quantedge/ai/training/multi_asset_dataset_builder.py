"""
Multi-Asset Real Market Dataset Builder and Cross-Asset Generalization Splitter.

Audits and ingests canonical historical market data for 4 instruments:
- BTCUSD, ETHUSD, SOLUSD, XRPUSD

Extracts causal SMC trade setups, applies deterministic clustering before splitting,
and compiles multi-asset training datasets preserving the canonical 24-feature contract.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    REAL_TARGET_NAMES,
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


@dataclass(frozen=True)
class AssetDataAudit:
    """Audit record for a single instrument's historical dataset."""
    symbol: str
    timeframe: str
    file_path: str
    available: bool
    status: str  # "AVAILABLE", "NOT_AVAILABLE", "INSUFFICIENT_HISTORY", "INVALID_DATA"
    training_status: str  # "TRAINABLE", "NOT_TRAINABLE"
    execution_authority: str  # "AUTHORIZED_IF_PROMOTED", "BLOCKED"
    candle_count: int
    start_timestamp: Optional[str]
    end_timestamp: Optional[str]
    missing_candles: int
    duplicate_candles: int
    ohlc_valid: bool
    volume_valid: bool
    file_size_bytes: int
    sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def audit_canonical_datasets(canonical_base: Optional[Path] = None) -> List[AssetDataAudit]:
    """
    Audits all 4 canonical instruments for Delta Exchange India.
    Never manufactures missing data.
    """
    if canonical_base is None:
        canonical_base = _get_repo_root() / "data" / "canonical" / "delta_exchange_india"

    target_symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    audits = []

    for sym in target_symbols:
        csv_file = canonical_base / sym / "1h" / "2026.csv"
        report = CanonicalDataValidator.validate_file(csv_file, symbol=sym, timeframe="1h")

        if not report.file_exists:
            audits.append(
                AssetDataAudit(
                    symbol=sym,
                    timeframe="1h",
                    file_path=str(csv_file),
                    available=False,
                    status="NOT_AVAILABLE",
                    training_status="NOT_TRAINABLE",
                    execution_authority="BLOCKED",
                    candle_count=0,
                    start_timestamp=None,
                    end_timestamp=None,
                    missing_candles=0,
                    duplicate_candles=0,
                    ohlc_valid=False,
                    volume_valid=False,
                    file_size_bytes=0,
                    sha256="MISSING",
                )
            )
            continue

        is_trainable = report.status == "VALIDATED_CLEAN" and report.candle_count >= 1000
        audits.append(
            AssetDataAudit(
                symbol=sym,
                timeframe="1h",
                file_path=str(csv_file),
                available=True,
                status="AVAILABLE" if is_trainable else report.status,
                training_status="TRAINABLE" if is_trainable else "NOT_TRAINABLE",
                execution_authority="AUTHORIZED_IF_PROMOTED" if is_trainable else "BLOCKED",
                candle_count=report.candle_count,
                start_timestamp=report.first_timestamp,
                end_timestamp=report.last_timestamp,
                missing_candles=report.gap_count,
                duplicate_candles=report.duplicate_count,
                ohlc_valid=report.is_valid_ohlc,
                volume_valid=report.is_valid_volume,
                file_size_bytes=report.file_size_bytes,
                sha256=report.sha256,
            )
        )

    return audits


@dataclass(frozen=True)
class ClusteredSetupSummary:
    """Summary of setup clustering & correlation analysis."""
    total_raw_setups: int
    clustered_within_3h: int
    clustered_percentage: float
    unique_structural_events: int
    mean_cluster_size: float
    max_cluster_size: int


def cluster_and_deduplicate_setups(
    df: pd.DataFrame, cluster_window_hours: float = 3.0, price_tol_pct: float = 0.05
) -> Tuple[pd.DataFrame, ClusteredSetupSummary]:
    """
    Identifies and groups correlated or clustered setups within a temporal window
    (default <= 3 hours) or sharing near-identical entry geometry.
    """
    if len(df) == 0:
        return df, ClusteredSetupSummary(0, 0, 0.0, 0, 0.0, 0)

    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    cluster_ids = np.zeros(len(df_sorted), dtype=int)
    current_cluster = 0
    cluster_ids[0] = current_cluster

    cluster_sizes = [1]

    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i - 1]
        curr = df_sorted.iloc[i]

        t_diff_h = (curr["timestamp"] - prev["timestamp"]).total_seconds() / 3600.0
        same_direction = prev["direction_long"] == curr["direction_long"]
        same_symbol = prev.get("symbol", "") == curr.get("symbol", "")

        if t_diff_h <= cluster_window_hours and same_direction and same_symbol:
            cluster_ids[i] = current_cluster
            cluster_sizes[-1] += 1
        else:
            current_cluster += 1
            cluster_ids[i] = current_cluster
            cluster_sizes.append(1)

    df_sorted["cluster_id"] = cluster_ids
    unique_events = current_cluster + 1
    clustered_count = len(df_sorted) - unique_events
    clustered_pct = (clustered_count / len(df_sorted)) * 100.0

    # Pick the primary representative setup per cluster (the first setup)
    dedup_indices = []
    seen = set()
    for idx, c_id in enumerate(cluster_ids):
        if c_id not in seen:
            seen.add(c_id)
            dedup_indices.append(idx)

    dedup_df = df_sorted.iloc[dedup_indices].reset_index(drop=True)

    summary = ClusteredSetupSummary(
        total_raw_setups=len(df_sorted),
        clustered_within_3h=clustered_count,
        clustered_percentage=round(clustered_pct, 1),
        unique_structural_events=unique_events,
        mean_cluster_size=round(float(np.mean(cluster_sizes)), 2),
        max_cluster_size=int(np.max(cluster_sizes)),
    )

    return dedup_df, summary


class MultiAssetDatasetBuilder:
    """Constructs multi-asset training datasets across all available canonical instruments."""

    def __init__(self, canonical_base: Optional[Path] = None):
        self.canonical_base = canonical_base or (_get_repo_root() / "data" / "canonical" / "delta_exchange_india")
        self.audits = audit_canonical_datasets(self.canonical_base)

    def get_available_symbols(self) -> List[str]:
        return [a.symbol for a in self.audits if a.status == "AVAILABLE"]

    def build_dataset_for_symbol(self, symbol: str) -> pd.DataFrame:
        csv_path = self.canonical_base / symbol / "1h" / "2026.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"Canonical dataset for {symbol} not available at {csv_path}")
        df = build_real_training_dataset(csv_path=csv_path, verbose=False)
        df["symbol"] = symbol
        return df

    def build_all_available_datasets(self) -> Dict[str, pd.DataFrame]:
        datasets = {}
        for sym in self.get_available_symbols():
            print(f"[MultiAsset] Ingesting canonical dataset for {sym}...")
            df = self.build_dataset_for_symbol(sym)
            datasets[sym] = df
            print(f"[MultiAsset] Extracted {len(df)} setups for {sym}.")
        return datasets

    def build_pooled_dataset(self) -> pd.DataFrame:
        all_ds = self.build_all_available_datasets()
        if not all_ds:
            return pd.DataFrame()
        pooled = pd.concat(all_ds.values(), ignore_index=True)
        pooled = pooled.sort_values("timestamp").reset_index(drop=True)
        return pooled

    def build_leave_one_asset_out_splits(
        self, test_symbol: str
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Creates a Leave-One-Asset-Out (LOAO) split.
        Train split = all symbols except test_symbol.
        Test split = test_symbol only.
        """
        all_ds = self.build_all_available_datasets()
        if test_symbol not in all_ds:
            raise ValueError(f"Held-out symbol {test_symbol} not found in available datasets: {list(all_ds.keys())}")

        test_df = all_ds[test_symbol].sort_values("timestamp").reset_index(drop=True)
        train_dfs = [df for sym, df in all_ds.items() if sym != test_symbol]
        train_df = pd.concat(train_dfs, ignore_index=True).sort_values("timestamp").reset_index(drop=True)

        return train_df, test_df
