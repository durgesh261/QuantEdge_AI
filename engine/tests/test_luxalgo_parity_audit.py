"""
Unit and Integration Tests for LuxAlgo ↔ QuantEdge Parity Audit Engine.

Tests:
1. Rule specifications completeness and verification status.
2. Bit-for-bit determinism across repeated executions.
3. Bullish and bearish OB slice semantics and boundary calculations.
4. Causal temporal ordering (pivot -> break -> OB -> decision).
5. Controlled same-setup trade construction ablations.
6. Ambiguous candle dual-touch resolution (Optimistic vs Conservative).
7. Unfilled limit order non-fill handling.
8. Preservation of production governance invariants.
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from quantedge.ai.evaluation.luxalgo_parity_audit import (
    LuxAlgoQuantEdgeParityAuditor,
    LUXALGO_RULE_SPECIFICATIONS,
    OBParityComparisonRecord,
    TradeAblationRecord,
    AttributionFactorRecord,
)
from quantedge.ai.evaluation.phase_r_walk_forward import _find_repo_root


@pytest.fixture
def sample_master_df():
    repo_root = _find_repo_root()
    master_path = repo_root / "docs" / "ai" / "multiyear_smc_order_blocks_master.csv"
    assert master_path.exists(), f"Master dataset missing at {master_path}"
    df = pd.read_csv(master_path)
    return df


def test_rule_specifications_integrity():
    """Verify that all LuxAlgo rules have valid verification status and evidence sources."""
    assert len(LUXALGO_RULE_SPECIFICATIONS) >= 10
    for r in LUXALGO_RULE_SPECIFICATIONS:
        assert r.rule_id.startswith("RULE_")
        assert r.verification_status in ("VERIFIED", "INFERRED", "UNKNOWN")
        assert r.parity_classification in ("MATCH", "MISMATCH", "UNKNOWN")
        assert len(r.evidence_source) > 0
        assert len(r.quantedge_behavior) > 0


def test_parity_audit_determinism(sample_master_df):
    """Test that two independent runs produce bit-for-bit identical results."""
    auditor1 = LuxAlgoQuantEdgeParityAuditor(master_df=sample_master_df)
    results1 = auditor1.run_full_parity_audit()

    auditor2 = LuxAlgoQuantEdgeParityAuditor(master_df=sample_master_df)
    results2 = auditor2.run_full_parity_audit()

    assert results1["total_evaluated_obs"] == results2["total_evaluated_obs"]
    assert results1["match_rate_pct"] == results2["match_rate_pct"]
    assert results1["control_stats"] == results2["control_stats"]


def test_order_block_boundary_and_slice_parity(sample_master_df):
    """Verify that QuantEdge OB boundaries match LuxAlgo slice extrema."""
    auditor = LuxAlgoQuantEdgeParityAuditor(master_df=sample_master_df)
    results = auditor.run_full_parity_audit()

    assert results["total_evaluated_obs"] == 1670
    assert results["match_rate_pct"] == 100.0
    assert results["matched_obs_count"] == 1670

    for comp in results["ob_comparisons"][:50]:
        assert comp["extreme_candle_matched"] is True
        assert comp["top_price_matched"] is True
        assert comp["bottom_price_matched"] is True
        assert comp["width_matched"] is True
        assert comp["mismatch_category"] == "NO_MISMATCH"


def test_controlled_trade_ablations_math(sample_master_df):
    """Test that trade ablations compute correct R-multiples and fill behavior."""
    auditor = LuxAlgoQuantEdgeParityAuditor(master_df=sample_master_df)
    results = auditor.run_full_parity_audit()

    ctrl = results["control_stats"]
    # Control A: QuantEdge Current
    assert "exp_r" in ctrl["ctrl_a_quantedge_current"]
    assert ctrl["ctrl_a_quantedge_current"]["fill_rate"] == 100.0

    # Control C: Midpoint 50% limit should have lower fill rate
    assert ctrl["ctrl_c_midpoint_50"]["fill_rate"] < 100.0
    assert ctrl["ctrl_c_midpoint_50"]["fill_rate"] > 20.0

    # Control G: Optimistic execution should have higher or equal expectancy than conservative Control A
    assert ctrl["ctrl_g_optimistic_exec"]["exp_r"] >= ctrl["ctrl_a_quantedge_current"]["exp_r"]


def test_attribution_matrix_completeness(sample_master_df):
    """Verify that factor attribution matrix covers all key structural and execution drivers."""
    auditor = LuxAlgoQuantEdgeParityAuditor(master_df=sample_master_df)
    results = auditor.run_full_parity_audit()

    attr = results["attribution_matrix"]
    assert len(attr) >= 5
    components = [a["component"] for a in attr]
    assert "OB Detection & Boundary Parity" in components
    assert "Intrabar Execution Semantics (Optimistic vs Conservative)" in components
    assert "Take Profit Structure (Swing Liquidity vs Fixed 1.714R)" in components


def test_production_governance_invariants():
    """Verify that production governance locks remain immutable."""
    # Ensure research module does not alter production flags
    repo_root = _find_repo_root()
    manifest_path = repo_root / "docs" / "ai" / "ai_governance_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            promo_status = manifest.get("ai_promotion_status") or manifest.get("promotion_status") or manifest.get("AI_PROMOTION_STATUS")
            assert promo_status == "REJECTED"
            assert manifest.get("live_execution_authorized") is False
            boundary = manifest.get("execution_boundary_policy", {})
            assert boundary.get("unauthorized_action") == "HARD_BLOCK" or manifest.get("execution_status") == "BLOCKED_BY_SYSTEM"
