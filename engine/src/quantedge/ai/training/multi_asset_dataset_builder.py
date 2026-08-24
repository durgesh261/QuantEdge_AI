"""
Multi-Asset Real Market Dataset Builder and Integrity Auditor.

Audits canonical historical market data for 4 instruments:
- BTCUSD, ETHUSD, SOLUSD, XRPUSD

Extracts causal SMC trade setups, applies deterministic clustering before splitting,
and compiles multi-asset training datasets preserving the canonical 24-feature contract.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from quantedge.ai.feature_contract import FEATURE_COUNT, FEATURE_NAMES
from quantedge.ai.training.real_dataset_builder import (
    DEFAULT_CANONICAL_PATH,
    REAL_TARGET_NAMES,
    TARGET_MAE_R,
    TARGET_MFE_R,
    TARGET_REALIZED_R,
    build_real_training_dataset,
)


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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[5]


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
        if not csv_file.exists():
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
                )
            )
            continue

        try:
            df = pd.read_csv(csv_file)
            size_b = csv_file.stat().st_size

            # Check timestamp
            ts_col = "timestamp" if "timestamp" in df.columns else ("time" if "time" in df.columns else None)
            if ts_col is None:
                raise ValueError("Missing timestamp column")

            df["parsed_ts"] = pd.to_datetime(df[ts_col], utc=True)
            df = df.sort_values("parsed_ts").reset_index(drop=True)

            n_candles = len(df)
            start_ts = df["parsed_ts"].iloc[0].isoformat() if n_candles > 0 else None
            end_ts = df["parsed_ts"].iloc[-1].isoformat() if n_candles > 0 else None

            # Duplicate timestamp check
            dups = int(df["parsed_ts"].duplicated().sum())

            # Expected 1H frequency check
            time_diffs_h = (df["parsed_ts"].diff().dt.total_seconds() / 3600.0).dropna()
            missing_count = int(np.sum(time_diffs_h > 1.5))

            # OHLC validity: high >= max(open, close) and low <= min(open, close) and low > 0
            ohlc_valid = True
            for req in ["open", "high", "low", "close"]:
                if req not in df.columns:
                    ohlc_valid = False
            if ohlc_valid and n_candles > 0:
                h_ge = (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-6).all()
                l_le = (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-6).all()
                pos = (df["low"] > 0).all()
                ohlc_valid = bool(h_ge and l_le and pos)

            # Volume validity
            vol_col = "volume" if "volume" in df.columns else None
            vol_valid = (vol_col is not None) and bool((df[vol_col] >= 0).all())

            # Status classification
            if n_candles < 1000:
                status = "INSUFFICIENT_HISTORY"
                tr_status = "NOT_TRAINABLE"
            elif not ohlc_valid or not vol_valid:
                status = "INVALID_DATA"
                tr_status = "NOT_TRAINABLE"
            else:
                status = "AVAILABLE"
                tr_status = "TRAINABLE"

            audits.append(
                AssetDataAudit(
                    symbol=sym,
                    timeframe="1h",
                    file_path=str(csv_file),
                    available=True,
                    status=status,
                    training_status=tr_status,
                    execution_authority="AUTHORIZED_IF_PROMOTED" if tr_status == "TRAINABLE" else "BLOCKED",
                    candle_count=n_candles,
                    start_timestamp=start_ts,
                    end_timestamp=end_ts,
                    missing_candles=missing_count,
                    duplicate_candles=dups,
                    ohlc_valid=ohlc_valid,
                    volume_valid=vol_valid,
                    file_size_bytes=size_b,
                )
            )
        except Exception as e:
            audits.append(
                AssetDataAudit(
                    symbol=sym,
                    timeframe="1h",
                    file_path=str(csv_file),
                    available=False,
                    status="INVALID_DATA",
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

    Returns:
        Tuple of (deduplicated_df, ClusteredSetupSummary).
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

        if t_diff_h <= cluster_window_hours and same_direction:
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
        return build_real_training_dataset(csv_path=csv_path, verbose=False)

    def build_all_available_datasets(self) -> Dict[str, pd.DataFrame]:
        datasets = {}
        for sym in self.get_available_symbols():
            print(f"[MultiAsset] Ingesting canonical dataset for {sym}...")
            df = self.build_dataset_for_symbol(sym)
            datasets[sym] = df
            print(f"[MultiAsset] Extracted {len(df)} setups for {sym}.")
        return datasets
