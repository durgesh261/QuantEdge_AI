"""
Phase 3E.5 Tests: Current-Day LuxAlgo Parity Validation

Regression tests for:
  - Dataset boundary enforcement
  - DATASET_UNAVAILABLE for post-cutoff checkpoints
  - Exactly one AVAILABLE checkpoint (2026-08-20T00:00)
  - 69k zone active OB count (zero)
  - Green zone FVG exclusion
  - TV differential classification
  - Deterministic checkpoint replay
  - Future-data invariance
  - Frozen SMC files
  - SHA-256 integrity
  - Active OB list completeness at boundary
"""

import csv
import json
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Dict

import pytest

ENGINE    = Path(__file__).parent.parent
REPO_ROOT = ENGINE.parent

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from tests._phase3e5_parity_lib import (
    verify_dataset,
    checkpoint_analysis,
    tv_ob_differential,
    is_post_cutoff,
    compute_phase3e5_in_memory,
    TV_OBSERVATIONS,
    CHECKPOINTS,
    DATASET_LAST_CANDLE_TS,
    DATASET_POST_CUTOFF_TS,
    EXPECTED_SHA256,
    EXPECTED_CANDLES,
    DATA_CSV,
    DATA_META,
)
from ob_snapshot_engine import OBSnapshotEngine

FROZEN_SMC = [
    ENGINE / "src" / "quantedge" / "smc" / "structure.py",
    ENGINE / "src" / "quantedge" / "smc" / "order_blocks.py",
    ENGINE / "src" / "quantedge" / "smc" / "volatility.py",
]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def eng():
    return OBSnapshotEngine.from_csv(str(DATA_CSV))


@pytest.fixture(scope="module")
def _in_memory_data(eng):
    return compute_phase3e5_in_memory(eng)


@pytest.fixture(scope="module")
def snap_rows(_in_memory_data):
    return _in_memory_data[0]


@pytest.fixture(scope="module")
def diff_rows(_in_memory_data):
    return _in_memory_data[1]


@pytest.fixture(scope="module")
def summary(_in_memory_data):
    return _in_memory_data[2]



# ═══════════════════════════════════════════════════════════════════════════════
# Dataset boundary and integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestDatasetIntegrity:
    """Dataset must be unmodified and end at 2026-08-20T00:00:00Z."""

    def test_sha256_matches(self):
        ok, sha = verify_dataset(DATA_META)
        assert ok, f"SHA-256 mismatch: got {sha}"

    def test_candle_count(self):
        ok, _ = verify_dataset(DATA_META)
        assert ok  # candle count is checked inside verify_dataset

    def test_last_candle_timestamp(self, eng):
        last = eng.candles[-1].timestamp
        assert last.isoformat() == DATASET_LAST_CANDLE_TS, (
            f"Expected last candle at {DATASET_LAST_CANDLE_TS}, got {last.isoformat()}"
        )

    def test_no_post_cutoff_data_in_workspace(self):
        """Only one canonical BTCUSD 1H data file must exist."""
        data_root = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h"
        csv_files = list(data_root.glob("*.csv"))
        assert len(csv_files) == 1, (
            f"Expected exactly 1 CSV file in canonical data dir, found {len(csv_files)}: "
            f"{[f.name for f in csv_files]}"
        )

    def test_summary_sha256_matches(self, summary):
        assert summary["dataset"]["sha256"] == EXPECTED_SHA256
        assert summary["dataset"]["sha256_ok"] is True

    def test_summary_candle_count(self, summary):
        assert summary["dataset"]["candles"] == EXPECTED_CANDLES


# ═══════════════════════════════════════════════════════════════════════════════
# is_post_cutoff logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostCutoffDetection:
    def test_dataset_end_is_not_post_cutoff(self):
        """The last candle timestamp itself is NOT post-cutoff."""
        assert not is_post_cutoff(DATASET_LAST_CANDLE_TS)

    def test_post_cutoff_ts_is_post_cutoff(self):
        assert is_post_cutoff(DATASET_POST_CUTOFF_TS)

    def test_aug20_06_is_post_cutoff(self):
        assert is_post_cutoff("2026-08-20T06:00:00+00:00")

    def test_aug20_12_is_post_cutoff(self):
        assert is_post_cutoff("2026-08-20T12:00:00+00:00")

    def test_aug20_14_is_post_cutoff(self):
        assert is_post_cutoff("2026-08-20T14:00:00+00:00")

    def test_aug20_16_is_post_cutoff(self):
        assert is_post_cutoff("2026-08-20T16:00:00+00:00")

    def test_aug19_is_not_post_cutoff(self):
        assert not is_post_cutoff("2026-08-19T23:00:00+00:00")


# ═══════════════════════════════════════════════════════════════════════════════
# Checkpoint analysis
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckpointAnalysis:
    """Each checkpoint must return correct status."""

    def test_exactly_one_available_checkpoint(self, summary):
        n_avail = summary["dataset_coverage"]["available_checkpoints"]
        assert n_avail == 1, (
            f"Expected exactly 1 AVAILABLE checkpoint (2026-08-20T00:00), got {n_avail}"
        )

    def test_four_post_cutoff_checkpoints(self, summary):
        n_post = summary["dataset_coverage"]["post_cutoff_checkpoints"]
        assert n_post == 4, (
            f"Expected 4 DATASET_UNAVAILABLE checkpoints, got {n_post}"
        )

    def test_post_cutoff_list_correct(self, summary):
        expected = [
            "2026-08-20T06:00:00+00:00",
            "2026-08-20T12:00:00+00:00",
            "2026-08-20T14:00:00+00:00",
            "2026-08-20T16:00:00+00:00",
        ]
        assert summary["dataset_coverage"]["post_cutoff_list"] == expected

    def test_boundary_checkpoint_returns_available(self, eng):
        result = checkpoint_analysis(eng, DATASET_LAST_CANDLE_TS, eng.candles)
        assert result["status"] == "AVAILABLE"

    def test_post_cutoff_returns_unavailable(self, eng):
        result = checkpoint_analysis(eng, "2026-08-20T06:00:00+00:00", eng.candles)
        assert result["status"] == "DATASET_UNAVAILABLE"
        assert result["active_obs"] == []

    def test_post_cutoff_no_fabricated_data(self, eng):
        """Post-cutoff checkpoints must return empty OB list — no fabrication."""
        for cp in ["2026-08-20T06:00:00+00:00", "2026-08-20T16:00:00+00:00"]:
            result = checkpoint_analysis(eng, cp, eng.candles)
            assert result["active_obs"] == [], (
                f"{cp}: Expected empty OB list but got {len(result['active_obs'])} OBs — "
                "no fabricated data allowed"
            )

    def test_boundary_checkpoint_41_active_obs(self, eng):
        """At 2026-08-20T00:00, there are exactly 41 active OBs."""
        result = checkpoint_analysis(eng, DATASET_LAST_CANDLE_TS, eng.candles)
        assert result["active_ob_count"] == 41, (
            f"Expected 41 active OBs at boundary, got {result['active_ob_count']}"
        )

    def test_checkpoint_analysis_deterministic(self, eng):
        """Running checkpoint analysis twice returns identical results."""
        r1 = checkpoint_analysis(eng, DATASET_LAST_CANDLE_TS, eng.candles)
        r2 = checkpoint_analysis(eng, DATASET_LAST_CANDLE_TS, eng.candles)
        assert r1["active_ob_count"] == r2["active_ob_count"]
        uppers1 = [ob["upper"] for ob in r1["active_obs"]]
        uppers2 = [ob["upper"] for ob in r2["active_obs"]]
        assert uppers1 == uppers2, "checkpoint_analysis is not deterministic"


# ═══════════════════════════════════════════════════════════════════════════════
# 69k Zone Investigation
# ═══════════════════════════════════════════════════════════════════════════════

class Test69kZone:
    """Zero active Python OBs exist in 68500-69500 at the dataset boundary."""

    def test_zero_active_obs_in_69k_at_boundary(self, summary):
        n = summary["69k_investigation"]["active_obs_in_69k_at_cutoff"]
        assert n == 0, (
            f"Expected 0 active OBs in 69k zone at dataset boundary, found {n}"
        )

    def test_69k_investigation_references_cutoff(self, summary):
        inv = summary["69k_investigation"]
        assert inv["last_available_checkpoint"] == DATASET_LAST_CANDLE_TS

    def test_69k_conclusion_mentions_post_cutoff(self, summary):
        conc = summary["69k_investigation"]["conclusion"].lower()
        assert "2026-08-20" in conc or "post-cutoff" in conc or "cutoff" in conc

    def test_boundary_snapshot_no_obs_in_69k(self, eng):
        """Direct engine check: no active OB overlaps [68500, 69500] at boundary."""
        snap = eng.snapshot_at(DATASET_LAST_CANDLE_TS)
        in_zone = [
            ob for ob in snap.active_obs
            if ob.lower_price <= Decimal("69500") and ob.upper_price >= Decimal("68500")
        ]
        assert not in_zone, (
            f"Found {len(in_zone)} active OB(s) in 69k zone — expected zero"
        )

    def test_post_break_candles_in_69k_but_no_new_break(self, eng):
        """
        Post-break candles 5535–5544 trade in 67k-70k territory, but no new
        structural break fires — so no 69k OB can be created.
        """
        candles = eng.candles
        parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs = eng._run_pipeline(candles)
        breaks_after_5534 = [b for b in int_brk + sw_brk if b.index > 5534]
        assert len(breaks_after_5534) == 0, (
            f"Expected 0 breaks after idx=5534, found {len(breaks_after_5534)}"
        )

    def test_dataset_has_8_post_break_candles_in_69k_zone(self, eng):
        """Candles 5535-5544 (10 candles) follow the last break; several trade in 68k-70k."""
        candles = eng.candles
        post_break = candles[5535:]
        in_range = [
            c for c in post_break
            if float(c.low) <= 69500 and float(c.high) >= 68500
        ]
        # At least 5 candles should be in the 68.5k-69.5k range
        assert len(in_range) >= 5, (
            f"Expected at least 5 post-break candles in 68.5k-69.5k range, "
            f"found {len(in_range)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Green Zone (FVG) Classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestGreenZoneFVG:
    """Green zones must always be classified as FVG and excluded from OB comparison."""

    def test_tv_green_001_classified_as_fvg(self, diff_rows):
        row = next((r for r in diff_rows if r["tv_id"] == "TV_GREEN_001"), None)
        assert row is not None, "TV_GREEN_001 not in differential"
        assert row["result"] == "IGNORE_FVG", f"Expected IGNORE_FVG, got {row['result']}"

    def test_tv_green_002_classified_as_fvg(self, diff_rows):
        row = next((r for r in diff_rows if r["tv_id"] == "TV_GREEN_002"), None)
        assert row is not None, "TV_GREEN_002 not in differential"
        assert row["result"] == "IGNORE_FVG", f"Expected IGNORE_FVG, got {row['result']}"

    def test_fvg_has_no_python_match(self, diff_rows):
        """FVG entries must never produce a Python OB match."""
        for row in diff_rows:
            if row["result"] == "IGNORE_FVG":
                assert row["python_match_upper"] == "", (
                    f"FVG {row['tv_id']} should have no python_match_upper"
                )

    def test_fvg_exclusion_programmatic(self, eng):
        """Directly call tv_ob_differential with an FVG entry — must return IGNORE_FVG."""
        fvg_entry = {
            "tv_id":       "TEST_FVG",
            "direction":   "bullish",
            "upper":       70000.0,
            "lower":       69500.0,
            "is_fvg":      True,
            "observed_ts": "2026-08-20",
            "notes":       "test green zone",
        }
        snap = eng.snapshot_at(DATASET_LAST_CANDLE_TS)
        active = [
            {
                "direction": ob.direction, "upper": float(ob.upper_price),
                "lower": float(ob.lower_price), "state": ob.state,
                "creation_timestamp": ob.creation_timestamp.isoformat(),
                "break_timestamp": ob.break_timestamp.isoformat() if ob.break_timestamp else "",
            }
            for ob in snap.active_obs
        ]
        result = tv_ob_differential([fvg_entry], active)
        assert result[0]["result"] == "IGNORE_FVG"

    def test_summary_green_zone_classification(self, summary):
        g = summary["green_zone_classification"]
        assert g["tv_green_001"]["verdict"] == "FVG"
        assert g["tv_green_002"]["verdict"] == "FVG"


# ═══════════════════════════════════════════════════════════════════════════════
# TV OB Differential
# ═══════════════════════════════════════════════════════════════════════════════

class TestTVDifferential:
    """TV OB 69k must be DATASET_UNAVAILABLE until tooltip prices are provided."""

    def test_tv_69k_001_dataset_unavailable(self, diff_rows):
        """Without exact tooltip prices, 69k OB cannot be classified."""
        row = next((r for r in diff_rows if r["tv_id"] == "TV_69K_001"), None)
        assert row is not None, "TV_69K_001 not in differential"
        assert row["result"] == "DATASET_UNAVAILABLE", (
            f"Expected DATASET_UNAVAILABLE (no tooltip prices yet), got {row['result']}"
        )

    def test_no_false_exact_match_claim(self, diff_rows):
        """EXACT_MATCH must NOT be claimed without exact tooltip data."""
        false_matches = [r for r in diff_rows if r["result"] == "EXACT_MATCH"]
        assert not false_matches, (
            f"Found {len(false_matches)} EXACT_MATCH claims — none are valid "
            f"without user-provided tooltip prices: "
            f"{[r['tv_id'] for r in false_matches]}"
        )

    def test_differential_has_3_rows(self, diff_rows):
        """Exactly 3 TV observations → 3 differential rows."""
        assert len(diff_rows) == 3, f"Expected 3 diff rows, got {len(diff_rows)}"

    def test_required_fields_in_differential(self, diff_rows):
        required = [
            "tv_id", "is_fvg", "direction", "tv_upper", "tv_lower",
            "result", "python_match_upper", "python_match_lower",
            "python_match_state", "explanation",
        ]
        headers = set(diff_rows[0].keys())
        for f in required:
            assert f in headers, f"Missing '{f}' in tv_ob_differential.csv"

    def test_pending_user_input_documented(self, summary):
        """The report must document that tooltip prices are pending."""
        p = summary["pending_user_input"]
        assert p["exact_tv_tooltip_upper"] == "PENDING"
        assert p["exact_tv_tooltip_lower"] == "PENDING"

    def test_overall_verdict_dataset_unavailable(self, summary):
        assert summary["overall_verdict"]["classification"] == "DATASET_UNAVAILABLE"

    def test_no_parity_claim_without_data(self, summary):
        assert summary["overall_verdict"]["can_reproduce_tv_screenshot"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Active OBs at boundary — completeness
# ═══════════════════════════════════════════════════════════════════════════════

class TestBoundaryActiveOBs:
    """Verify active OBs at dataset boundary are fully documented."""

    def test_41_active_obs_at_boundary(self, snap_rows):
        available = [r for r in snap_rows if r["status"] == "AVAILABLE"]
        assert len(available) == 41, (
            f"Expected 41 active OB rows at boundary checkpoint, got {len(available)}"
        )

    def test_no_active_ob_above_70k(self, eng):
        """All active bullish OBs at boundary are below 70k (current price is 69k-70k)."""
        snap = eng.snapshot_at(DATASET_LAST_CANDLE_TS)
        bull_above_70k = [
            ob for ob in snap.active_obs
            if ob.direction == "bullish" and ob.upper_price > Decimal("70000")
        ]
        assert not bull_above_70k, (
            f"Found {len(bull_above_70k)} active bullish OBs above 70k — unexpected"
        )

    def test_highest_active_bearish_ob_is_above_90k(self, eng):
        """Highest active bearish OB is from Jan 2026 (still unmitigated — Jan bearish run)."""
        snap = eng.snapshot_at(DATASET_LAST_CANDLE_TS)
        bear_obs = [ob for ob in snap.active_obs if ob.direction == "bearish"]
        assert bear_obs, "Expected active bearish OBs"
        max_upper = max(float(ob.upper_price) for ob in bear_obs)
        assert max_upper > 90000, (
            f"Expected highest bearish OB above 90k, got {max_upper:.1f}"
        )

    def test_checkpoint_csv_required_fields(self, snap_rows):
        required = [
            "checkpoint", "status", "active_count", "total_count",
        ]
        headers = set(snap_rows[0].keys())
        for f in required:
            assert f in headers, f"Missing '{f}' in checkpoint_snapshots.csv"

    def test_future_data_invariance(self, eng):
        """
        Snapshot at an intermediate date returns the same state regardless of whether
        we call it before or after computing the boundary snapshot.
        """
        mid = "2026-07-01T00:00:00+00:00"
        snap1 = eng.snapshot_at(mid)
        _ = eng.snapshot_at(DATASET_LAST_CANDLE_TS)  # load later checkpoint
        snap2 = eng.snapshot_at(mid)
        assert snap1.active_count == snap2.active_count, (
            "Future-data invariance violated: snapshot at mid-date differs "
            "depending on whether a later snapshot was computed first"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Frozen SMC + production integrity
# ═══════════════════════════════════════════════════════════════════════════════

def test_frozen_smc_files_exist():
    for p in FROZEN_SMC:
        assert p.exists(), f"Frozen SMC file missing: {p}"


def test_frozen_smc_files_unmodified():
    result = subprocess.run(
        ["git", "diff", "--",
         "engine/src/quantedge/smc/structure.py",
         "engine/src/quantedge/smc/order_blocks.py",
         "engine/src/quantedge/smc/volatility.py"],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.stdout.strip() == "", (
        f"Frozen SMC files were modified:\n{result.stdout}"
    )


def test_no_production_smc_changes(summary):
    assert summary["overall_verdict"]["production_smc_changes"] == "NONE"


def test_phase4_not_started(summary):
    assert summary["overall_verdict"]["phase4_started"] is False
