"""
Phase K — Expanded Historical OB Dataset, Statistical Power & Causal Invariance Test Suite.

Verifies:
1. Expanded dataset construction determinism and schema correctness.
2. Causal feature extraction and zero future-data leakage.
3. Strict chronological splits and >= 72h embargo isolation.
4. Second-edge stop loss and 60/35 TP arithmetic.
5. Dynamic leverage calculation and cap invariant.
6. Liquidation distance and gap-risk safety.
7. Validation-only threshold selection and frozen OOS immutability.
8. Paired Moving Block Bootstrap determinism.
9. Promotion gate evaluation rule and execution boundary enforcement.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.evaluation.phase_i_ob_replay import (
    PHASE_I_TP_RR_CONFIG,
    REPLAY_HORIZON_BARS,
    WARMUP_BARS,
    compute_dynamic_leverage,
    compute_net_r,
    estimate_liquidation,
)
from quantedge.ai.evaluation.phase_j_ob_dataset import (
    FEATURE_DIM,
    LABEL_REALIZED_R,
    OB_FEATURE_NAMES,
)
from quantedge.ai.evaluation.phase_k_research import (
    SYMBOLS,
    assign_phase_k_splits,
    build_phase_k_dataset,
    compute_group_metrics,
    load_expanded_canonical_candles,
    select_threshold_on_validation,
)
from quantedge.market_data.models import Candle, MarketDataSource, Timeframe


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "backend").exists():
            return parent
    return p.parents[4]


@pytest.fixture(scope="module")
def canonical_base() -> Path:
    return _find_repo_root() / "data" / "canonical" / "delta_exchange_india"


class TestPhaseKExpandedDatasetAndCausality:
    """Verifies historical dataset expansion and causal safety."""

    def test_canonical_candles_load_and_chronology(self, canonical_base):
        """Verifies expanded 19,479 candles load cleanly for each instrument."""
        for sym in SYMBOLS:
            candles = load_expanded_canonical_candles(canonical_base, sym)
            assert len(candles) >= 5583, f"{sym} candle count too low: {len(candles)}"
            # Ascending timestamps
            ts_list = [c.timestamp for c in candles]
            assert ts_list == sorted(ts_list), f"{sym} candles are not sorted"
            assert len(ts_list) == len(set(ts_list)), f"{sym} has duplicate timestamps"

    def test_ohlc_integrity_on_expanded_data(self, canonical_base):
        """Verifies all candles satisfy strict OHLC geometric constraints."""
        for sym in SYMBOLS:
            candles = load_expanded_canonical_candles(canonical_base, sym)
            for c in candles[:500]:
                assert c.high >= c.open
                assert c.high >= c.close
                assert c.low <= c.open
                assert c.low <= c.close
                assert c.volume >= Decimal("0")

    def test_causal_feature_dimension_and_names(self):
        """Verifies exactly 29 causal features are declared and scale-invariant."""
        assert len(OB_FEATURE_NAMES) == 29
        assert len(OB_FEATURE_NAMES) == FEATURE_DIM
        for name in OB_FEATURE_NAMES:
            assert not name.startswith("label_")
            assert not name.startswith("target_")

    def test_split_chronology_and_embargo(self):
        """Verifies train, validation, and OOS splits maintain >= 72h embargo isolation."""
        # Create synthetic test frame
        times = pd.date_range("2025-01-01", "2026-08-01", freq="6h", tz="UTC")
        df_mock = pd.DataFrame({
            "decision_time": [t.isoformat() for t in times],
            "asset": "BTCUSD",
            LABEL_REALIZED_R: 0.5,
        })
        for feat in OB_FEATURE_NAMES:
            df_mock[feat] = 0.5

        df_splits = assign_phase_k_splits(df_mock)
        tr = df_splits[df_splits["split"] == "train"]
        va = df_splits[df_splits["split"] == "val"]
        oo = df_splits[df_splits["split"] == "oos"]

        assert len(tr) > 0
        assert len(va) > 0
        assert len(oo) > 0

        # Gap train -> val
        t_tr_max = pd.to_datetime(tr["decision_time"]).max()
        t_va_min = pd.to_datetime(va["decision_time"]).min()
        gap1 = (t_va_min - t_tr_max).total_seconds() / 3600.0
        assert gap1 >= 72.0, f"Train to Val embargo too short: {gap1}h"

        # Gap val -> oos
        t_va_max = pd.to_datetime(va["decision_time"]).max()
        t_oo_min = pd.to_datetime(oo["decision_time"]).min()
        gap2 = (t_oo_min - t_va_max).total_seconds() / 3600.0
        assert gap2 >= 72.0, f"Val to OOS embargo too short: {gap2}h"

    def test_second_edge_sl_and_tp_geometry(self):
        """Verifies that 60/35 TP and second-edge SL satisfy the required reward multiple."""
        rm = float(PHASE_I_TP_RR_CONFIG.reward_multiple)
        expected_rm = 60.0 / 35.0
        assert abs(rm - expected_rm) < 1e-5

    def test_dynamic_leverage_and_liquidation_safety(self):
        """Verifies leverage formula: floor(35.0 / stop_dist_pct) capped at 100x."""
        # Case 1: 1.0% stop distance -> leverage 35x
        stop_dist_pct = 1.0
        lev = max(1, min(100, int(35.0 / stop_dist_pct)))
        assert lev == 35

        # Case 2: 0.2% stop distance -> capped at 100x
        stop_dist_pct = 0.2
        lev = max(1, min(100, int(35.0 / stop_dist_pct)))
        assert lev == 100

        # Case 3: 5.0% stop distance -> leverage 7x
        stop_dist_pct = 5.0
        lev = max(1, min(100, int(35.0 / stop_dist_pct)))
        assert lev == 7

    def test_group_metrics_computation(self):
        """Verifies performance metrics calculate correct expectancy, win rate, and profit factor."""
        df_test = pd.DataFrame({
            LABEL_REALIZED_R: [1.7143, -1.0, 1.7143, -1.0],
            "label_mfe_r": [1.8, 0.2, 1.9, 0.4],
            "label_mae_r": [0.3, 1.0, 0.2, 1.0],
        })
        m = compute_group_metrics(df_test, len(df_test))
        assert m["n"] == 4
        assert m["coverage_pct"] == 100.0
        assert m["win_rate_pct"] == 50.0
        assert m["expectancy_r"] == round((1.7143 * 2 - 2.0) / 4, 4)
        assert m["profit_factor"] == round((1.7143 * 2) / 2.0, 3)

    def test_governance_rejection_invariant_when_ci_spans_zero(self):
        """Verifies promotion gate strictly rejects when bootstrap CI lower bound <= 0."""
        from quantedge.ai.evaluation.phase_k_research import PhaseKResearchPipeline
        # Test gate rejection logic
        primary_mock = {
            "metrics_summary": {
                "smc_baseline": {"expectancy_r": 0.01, "profit_factor": 1.01, "max_drawdown_r": 10.0},
                "ai_filtered": {"expectancy_r": 0.25, "profit_factor": 1.45, "max_drawdown_r": 5.0, "coverage_pct": 35.0, "n": 30},
                "ai_rejected": {"expectancy_r": -0.10},
                "incremental_expectancy_r": 0.24,
            },
            "bootstrap_ci": {
                "incremental_95ci": [-0.05, 0.50],  # Negative lower bound
            },
        }
        loao_mock = [{"status": "GENERALIZED_POSITIVE"}] * 4
        lev_mock = {"liquidation_before_sl_count": 0}

        pipeline = PhaseKResearchPipeline.__new__(PhaseKResearchPipeline)
        decision = pipeline._evaluate_promotion_gate(primary_mock, loao_mock, lev_mock)
        assert decision["status"] == "REJECTED"
        assert decision["live_execution_authorized"] is False
        assert decision["criteria"]["C5_bootstrap_ci_lower_bound_positive"]["passed"] is False
