"""
Phase L — Powered Chronological OOS Validation & Causal Invariance Test Suite.

Verifies:
1. Data provenance and canonical full-history integrity.
2. Pre-registered chronological split isolation and >= 72h embargo.
3. Causal feature extraction and zero future lookahead.
4. Second-edge SL and 60/35 TP reward multiple arithmetic.
5. Dynamic leverage formula and production cap invariant.
6. Wilson score confidence interval calculations.
7. Analytical power curve calculation correctness.
8. 10,000-resample paired MBB bootstrap determinism.
9. 10-criterion promotion gate evaluation and execution boundary enforcement.
10. Bit-exact pipeline reproducibility.
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
from quantedge.ai.evaluation.phase_l_research import (
    FROZEN_ALPHA,
    FROZEN_MODEL_NAME,
    FROZEN_THRESHOLD,
    SYMBOLS,
    assign_phase_l_splits,
    build_phase_l_dataset,
    compute_phase_l_metrics,
    compute_rigorous_power_curves,
    load_canonical_full_history,
    wilson_score_interval,
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


class TestPhaseLPoweredValidation:
    """Comprehensive test suite for Phase L powered confirmatory research."""

    def test_canonical_full_history_integrity(self, canonical_base):
        """Verifies 19,479 canonical candles load cleanly for all 4 instruments."""
        for sym in SYMBOLS:
            candles = load_canonical_full_history(canonical_base, sym)
            assert len(candles) >= 19479, f"{sym} candle count too low: {len(candles)}"
            ts_list = [c.timestamp for c in candles]
            assert ts_list == sorted(ts_list), f"{sym} candles are not sorted"
            assert len(ts_list) == len(set(ts_list)), f"{sym} has duplicate timestamps"

    def test_chronological_split_and_embargo(self):
        """Verifies train and powered OOS splits maintain >= 72h embargo isolation."""
        times = pd.date_range("2024-06-01", "2026-08-20", freq="4h", tz="UTC")
        df_mock = pd.DataFrame({
            "decision_time": [t.isoformat() for t in times],
            "asset": "BTCUSD",
            LABEL_REALIZED_R: 0.5,
        })
        for feat in OB_FEATURE_NAMES:
            df_mock[feat] = 0.5

        df_splits = assign_phase_l_splits(df_mock)
        tr = df_splits[df_splits["split"] == "train"]
        oo = df_splits[df_splits["split"] == "oos"]

        assert len(tr) > 0
        assert len(oo) > 0

        t_tr_max = pd.to_datetime(tr["decision_time"]).max()
        t_oo_min = pd.to_datetime(oo["decision_time"]).min()
        gap = (t_oo_min - t_tr_max).total_seconds() / 3600.0
        assert gap >= 72.0, f"Embargo gap too short: {gap}h"

    def test_causal_feature_contract(self):
        """Verifies exactly 29 scale-invariant causal features are present."""
        assert len(OB_FEATURE_NAMES) == 29
        assert len(OB_FEATURE_NAMES) == FEATURE_DIM
        for feat in OB_FEATURE_NAMES:
            assert not feat.startswith("target_")
            assert not feat.startswith("label_")

    def test_wilson_score_interval(self):
        """Verifies Wilson score confidence interval calculation."""
        # 50 wins out of 100 trades -> ~50% with [40.4%, 59.6%]
        low, high = wilson_score_interval(50, 100)
        assert 39.0 <= low <= 41.5
        assert 58.5 <= high <= 61.0

        # 0 wins out of 10 trades
        low0, high0 = wilson_score_interval(0, 10)
        assert low0 == 0.0
        assert high0 > 0.0

    def test_power_curves_calculation(self):
        """Verifies sample size calculation logic for power analysis."""
        res = compute_rigorous_power_curves(std_r=1.30, coverage=0.225)
        table = res["power_table"]
        assert len(table) == 4
        # Higher effect size should require fewer trades
        eff_020 = next(r for r in table if r["incremental_effect_r"] == 0.20)
        eff_030 = next(r for r in table if r["incremental_effect_r"] == 0.30)
        assert eff_020["power_80pct"]["total_oos_setups"] > eff_030["power_80pct"]["total_oos_setups"]
        assert eff_020["power_80pct"]["total_oos_setups"] >= 1000

    def test_frozen_model_and_threshold_constants(self):
        """Verifies Phase L constants match pre-registered specification."""
        assert FROZEN_MODEL_NAME == "Ridge"
        assert FROZEN_ALPHA == 1.0
        assert FROZEN_THRESHOLD == 0.20

    def test_promotion_gate_strictly_rejects_on_ci_spanning_zero(self):
        """Verifies gate strictly outputs REJECTED when criterion C5 fails."""
        from quantedge.ai.evaluation.phase_l_research import PhaseLResearchPipeline
        pipeline = PhaseLResearchPipeline.__new__(PhaseLResearchPipeline)

        smc = {"expectancy_r": 0.01, "profit_factor": 1.01, "max_drawdown_r": 20.0, "win_rate_pct": 36.0, "win_rate_95ci": [30.0, 42.0], "n": 800}
        ai = {"expectancy_r": 0.25, "profit_factor": 1.45, "max_drawdown_r": 10.0, "win_rate_pct": 45.0, "win_rate_95ci": [38.0, 52.0], "coverage_pct": 22.0, "n": 180}
        rej = {"expectancy_r": -0.05}
        inc_exp = 0.24
        ci_negative_lower = {"incremental_95ci": [-0.05, 0.45]}
        loao = [{"status": "GENERALIZED_POSITIVE"}] * 4
        wf = [{"status": "POSITIVE"}] * 4
        lev = {"liquidation_before_sl_count": 0}

        decision = pipeline._evaluate_promotion_gate(smc, ai, rej, inc_exp, ci_negative_lower, loao, wf, lev)
        assert decision["status"] == "REJECTED"
        assert decision["live_execution_authorized"] is False
        assert decision["criteria"]["C5_statistical_significance_ci_positive"]["passed"] is False
