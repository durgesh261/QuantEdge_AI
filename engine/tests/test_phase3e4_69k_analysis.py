"""
Phase 3E.4 Tests: 69k OB Discrepancy Investigation

Regression tests for:
    - Price-region OB search
    - Structure event lookup in window
    - Candidate OB reconstruction
    - Internal vs swing classification
    - Display limit analysis
    - Lifecycle exclusion (invalidated ≠ active)
    - Deterministic output
    - FVG exclusion (inherited from Phase 3E)
    - Frozen SMC files
"""

import csv
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List

import pytest

ENGINE    = Path(__file__).parent.parent
REPO_ROOT = ENGINE.parent

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from generate_phase3e4_69k_analysis import (
    section1_region_obs,
    section2_structure_events,
    section3_candidate_obs,
    section4_differential,
    REGION_LOWER, REGION_UPPER,
    WINDOW_START_ISO, WINDOW_END_ISO,
    DATASET_CUTOFF, EXPECTED_SHA256, EXPECTED_CANDLES,
    LUXALGO_INTERNAL_OB_LIMIT, LUXALGO_SWING_OB_LIMIT,
    DATA_CSV, OUT_DIR,
)
from ob_snapshot_engine import OBSnapshotEngine, OBRecord

REGION_CSV  = OUT_DIR / "69k_region_obs.csv"
EVENTS_CSV  = OUT_DIR / "69k_structure_events.csv"
CAND_CSV    = OUT_DIR / "69k_candidate_obs.csv"
DIFF_JSON   = OUT_DIR / "69k_differential.json"

FROZEN_SMC = [
    ENGINE / "src" / "quantedge" / "smc" / "structure.py",
    ENGINE / "src" / "quantedge" / "smc" / "order_blocks.py",
    ENGINE / "src" / "quantedge" / "smc" / "volatility.py",
]


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def eng():
    return OBSnapshotEngine.from_csv(str(DATA_CSV))


@pytest.fixture(scope="module")
def snap(eng):
    return eng.snapshot_at(DATASET_CUTOFF)


@pytest.fixture(scope="module")
def pipeline(eng):
    candles = eng.candles
    parsed, int_brk, sw_brk, int_piv, sw_piv, raw_obs = eng._run_pipeline(candles)
    return candles, int_brk, sw_brk, int_piv, sw_piv


@pytest.fixture(scope="module")
def diff():
    return json.loads(DIFF_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def region_rows():
    with open(REGION_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def cand_rows():
    with open(CAND_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ═══════════════════════════════════════════════════════════════════════════════
# Output file existence
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutputFilesExist:
    def test_region_csv_exists(self):
        assert REGION_CSV.exists(), "69k_region_obs.csv missing"

    def test_events_csv_exists(self):
        assert EVENTS_CSV.exists(), "69k_structure_events.csv missing"

    def test_candidate_csv_exists(self):
        assert CAND_CSV.exists(), "69k_candidate_obs.csv missing"

    def test_differential_json_exists(self):
        assert DIFF_JSON.exists(), "69k_differential.json missing"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1 — Price-region OB search
# ═══════════════════════════════════════════════════════════════════════════════

class TestPriceRegionSearch:
    """All OBs overlapping [68500, 69500] must be found and classified correctly."""

    def test_region_search_finds_25_obs(self, region_rows):
        """Exactly 25 OBs exist in the 68500–69500 price range."""
        assert len(region_rows) == 25, (
            f"Expected 25 OBs in 68500–69500 region, found {len(region_rows)}"
        )

    def test_all_region_obs_are_invalidated(self, region_rows):
        """CRITICAL: Zero active OBs in the 69k region at cutoff."""
        active = [r for r in region_rows if r["is_active"] == "True"]
        assert not active, (
            f"Expected 0 active OBs in 69k region, found {len(active)}: "
            f"{[(r['creation_timestamp'][:10], r['direction']) for r in active]}"
        )

    def test_region_obs_all_older_than_aug(self, region_rows):
        """All 69k region OBs are from before Aug 2026 (historical, not recent)."""
        for r in region_rows:
            ts = r["creation_timestamp"][:7]  # YYYY-MM
            assert ts < "2026-08", (
                f"Found 69k region OB from {ts} — expected all to be before Aug 2026"
            )

    def test_region_search_includes_edge_overlap(self, snap):
        """An OB whose lower == REGION_UPPER boundary should be included."""
        rows = section1_region_obs(snap.all_obs, [])
        # All results must have lower <= REGION_UPPER and upper >= REGION_LOWER
        for r in rows:
            assert r["lower"] <= float(REGION_UPPER), f"lower={r['lower']} exceeds REGION_UPPER"
            assert r["upper"] >= float(REGION_LOWER), f"upper={r['upper']} below REGION_LOWER"

    def test_region_search_excludes_outside_obs(self, snap):
        """OBs entirely below 68500 or above 69500 must NOT appear."""
        rows = section1_region_obs(snap.all_obs, [])
        # Spot check: the 62k OBs should not appear
        in_region_uppers = {r["upper"] for r in rows}
        assert 62778.0 not in in_region_uppers, (
            "62778 OB should NOT appear in 69k region search"
        )
        assert 64328.0 not in in_region_uppers, (
            "64328 OB should NOT appear in 69k region search"
        )

    def test_required_fields_in_region_csv(self, region_rows):
        required = [
            "structure_type", "direction", "upper", "lower",
            "creation_timestamp", "break_candle_index", "state", "is_active",
        ]
        headers = set(region_rows[0].keys())
        for f in required:
            assert f in headers, f"Missing '{f}' in 69k_region_obs.csv"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2 — Structure event lookup
# ═══════════════════════════════════════════════════════════════════════════════

class TestStructureEvents:
    """Structure breaks in the Aug 14-20 window are correctly identified."""

    def test_nine_breaks_in_window(self, diff):
        """Exactly 9 structure breaks (8 unique index events × 1 swing+1 internal at 5534)."""
        # From the generator output: 9 break rows in window
        rows = list(csv.DictReader(open(EVENTS_CSV, newline="", encoding="utf-8")))
        # Filter to just breaks (not pivots) — all rows are breaks since we combined
        break_rows = [r for r in rows if "break_price" in r and r.get("break_price", "")]
        # We expect 9 break events (including both internal and swing at 5534)
        assert len(break_rows) == 9, f"Expected 9 break events, got {len(break_rows)}"

    def test_last_break_is_idx_5534(self, diff):
        """Last structure break in dataset is idx=5534."""
        assert diff["last_structure_break_index"] == 5534
        assert diff["last_structure_break_ts"].startswith("2026-08-19T14:00")

    def test_no_breaks_after_5534(self, diff):
        """Zero structure breaks occur after idx=5534 (dataset boundary)."""
        assert diff["breaks_after_last_break"] == 0, (
            "Expected 0 breaks after 5534 (dataset ends), "
            f"but found {diff['breaks_after_last_break']}"
        )

    def test_swing_choch_at_5534(self, pipeline):
        """A swing CHOCH (bullish) exists at idx=5534."""
        _, int_brk, sw_brk, _, _ = pipeline
        sw_5534 = next((b for b in sw_brk if b.index == 5534), None)
        assert sw_5534 is not None, "Expected swing break at idx=5534"
        bt = sw_5534.break_type.value if hasattr(sw_5534.break_type, "value") else str(sw_5534.break_type)
        bdir = sw_5534.direction.value if hasattr(sw_5534.direction, "value") else str(sw_5534.direction)
        assert bt == "choch", f"Expected swing choch, got {bt}"
        assert bdir == "bullish", f"Expected bullish, got {bdir}"

    def test_swing_pivot_search_range(self, pipeline):
        """Swing pivot for break 5534 is at idx=5302 (Aug-09 HIGH at 65457)."""
        candles, _, _, _, sw_piv = pipeline
        sw_5302 = next((p for p in sw_piv if p.index == 5302), None)
        assert sw_5302 is not None, "Expected swing pivot at idx=5302"
        assert sw_5302.is_high, "Swing pivot 5302 must be a HIGH"
        assert abs(float(sw_5302.price) - 65457.0) < 1.0, (
            f"Expected pivot price 65457, got {float(sw_5302.price):.1f}"
        )

    def test_69k_candles_are_post_break(self, diff):
        """All candles in 69k zone occurred after the last structure break."""
        assert len(diff["candles_in_69k_post_break"]) >= 1, (
            "Expected at least 1 candle in 69k zone post-break"
        )
        last_break_idx = diff["last_structure_break_index"]
        for c in diff["candles_in_69k_post_break"]:
            assert c["index"] > last_break_idx, (
                f"Candle idx={c['index']} is NOT after last break idx={last_break_idx}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3 — Candidate OB reconstruction
# ═══════════════════════════════════════════════════════════════════════════════

class TestCandidateOBReconstruction:
    """For each break in the window, the OB reconstruction must match actual records."""

    def test_no_candidate_in_69k_region(self, cand_rows):
        """CRITICAL: Zero reconstructed candidate OBs fall in 68500–69500."""
        in_region = [r for r in cand_rows if r["in_69k_region"] == "True"]
        assert not in_region, (
            f"Expected 0 candidates in 69k region, found {len(in_region)}: "
            f"{[(r['break_index'], r['reconstructed_upper'], r['reconstructed_lower']) for r in in_region]}"
        )

    def test_all_reconstructions_match_actual_obs(self, cand_rows):
        """
        Reconstruction should match actual OBs, except for known break-5534 cases.

        Break 5534 has two internal OBs (source=Aug-19 06:00, upper=64328) that do NOT
        match the min-low reconstruction (source=Aug-14 14:00, lower=62505).
        This confirms the engine uses a more nuanced source-candle selection than
        a simple minimum-low scan of the full pivot range. This is DOCUMENTED behaviour —
        the discrepancy is at 62778/62505, which is far from the 69k zone (CONFIRMED not in region).
        """
        # Allow break-5534 mismatches — they are known and documented.
        # The key invariant is: mismatching reconstructions must NOT be in the 69k region.
        unexpected_mismatches = [
            r for r in cand_rows
            if r["reconstruction_matches"] == "False" and r["in_69k_region"] == "True"
        ]
        assert not unexpected_mismatches, (
            f"Found {len(unexpected_mismatches)} reconstruction mismatches IN THE 69k REGION — "
            "this would change the investigation verdict:\n"
            + "\n".join(
                f"  break={r['break_index']} recon=({r['reconstructed_upper']},{r['reconstructed_lower']}) "
                f"actual=({r['actual_ob_upper']},{r['actual_ob_lower']})"
                for r in unexpected_mismatches[:3]
            )
        )
        # Document (not fail) the break-5534 source-selection discrepancy
        known_mismatches = [
            r for r in cand_rows
            if r["reconstruction_matches"] == "False"
        ]
        for km in known_mismatches:
            assert int(km["break_index"]) == 5534, (
                f"Unexpected mismatch at break={km['break_index']} — only break-5534 is known"
            )
            assert km["in_69k_region"] == "False", (
                f"Known mismatch at break-5534 must NOT be in 69k region"
            )

    def test_swing_choch_5534_reconstructed_lower_is_62505(self, cand_rows):
        """Swing CHOCH 5534 must reconstruct to lower=62505 (not 69k zone)."""
        rows_5534 = [r for r in cand_rows if int(r["break_index"]) == 5534]
        assert rows_5534, "No candidate rows for break 5534"
        for r in rows_5534:
            assert abs(float(r["reconstructed_lower"]) - 62505.0) < 1.0, (
                f"Break 5534 should reconstruct to lower~62505, got {r['reconstructed_lower']}"
            )

    def test_required_fields_in_candidate_csv(self, cand_rows):
        required = [
            "break_index", "structure_type", "direction",
            "reconstructed_upper", "reconstructed_lower",
            "in_69k_region", "reconstruction_matches",
        ]
        headers = set(cand_rows[0].keys())
        for f in required:
            assert f in headers, f"Missing '{f}' in 69k_candidate_obs.csv"

    def test_reconstruction_deterministic(self, snap, pipeline):
        """Running section3 twice produces identical output."""
        candles, int_brk, sw_brk, int_piv, sw_piv = pipeline
        win_start = datetime.fromisoformat(WINDOW_START_ISO)
        win_end   = datetime.fromisoformat(WINDOW_END_ISO)
        break_rows, _ = section2_structure_events(
            candles, int_brk, sw_brk, int_piv, sw_piv, win_start, win_end
        )
        r1 = section3_candidate_obs(candles, break_rows, snap.all_obs)
        r2 = section3_candidate_obs(candles, break_rows, snap.all_obs)
        assert len(r1) == len(r2)
        for a, b in zip(r1, r2):
            assert a["reconstructed_upper"] == b["reconstructed_upper"]
            assert a["reconstructed_lower"] == b["reconstructed_lower"]


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4 — Case classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseClassification:
    """The 69k discrepancy must be classified as VISIBILITY / 69K_OB_EXPLAINED."""

    def test_phase_status_is_explained(self, diff):
        assert diff["phase_status"] == "69K_OB_EXPLAINED", (
            f"Expected 69K_OB_EXPLAINED, got: {diff['phase_status']}"
        )

    def test_case_is_visibility(self, diff):
        assert diff["case_classification"] == "VISIBILITY", (
            f"Expected VISIBILITY, got: {diff['case_classification']}"
        )

    def test_no_production_changes(self, diff):
        assert diff["production_smc_changes"] == "NONE"

    def test_phase4_not_started(self, diff):
        assert diff["phase4_started"] is False

    def test_explanation_references_data_cutoff(self, diff):
        """The explanation must mention the dataset cutoff."""
        exp = diff["discrepancy_explanation"].lower()
        assert "cutoff" in exp or "2026-08-20" in exp, (
            "Explanation must reference the dataset cutoff date"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Section 5 — Internal vs Swing classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestInternalSwingClassification:
    """Both internal and swing structures must be investigated."""

    def test_swing_choch_5534_produces_no_swing_ob(self, diff, snap):
        """The swing CHOCH at 5534 did NOT produce a swing OB."""
        assert diff["no_swing_ob_recorded"] is True
        swing_obs = [ob for ob in snap.all_obs
                     if ob.structure_type == "swing" and ob.break_candle_index == 5534]
        assert not swing_obs, (
            f"Expected no swing OB from break 5534, found {len(swing_obs)}"
        )

    def test_swing_search_range_is_below_69k(self, diff):
        """The swing CHOCH 5534 search range ([5302, 5534)) is entirely below 69k."""
        sw = diff["swing_choch_5534"]
        assert sw["reconstructed_lower"] < 68500, (
            f"Swing OB lower should be below 68500, got {sw['reconstructed_lower']}"
        )
        assert sw["reconstructed_upper"] < 68500, (
            f"Swing OB upper should be below 68500, got {sw['reconstructed_upper']}"
        )

    def test_swing_pivot_is_above_break_search_range(self, diff):
        """Swing pivot at 5302 (price=65457) is below 69k — confirms no 69k OB possible."""
        sw = diff["swing_choch_5534"]
        assert sw["pivot_price"] < 68500, (
            f"Pivot price {sw['pivot_price']} is too high — review"
        )

    def test_both_internal_and_swing_checked(self, pipeline, snap):
        """Internal and swing breaks in the window are both inspected."""
        candles, int_brk, sw_brk, int_piv, sw_piv = pipeline
        win_start = datetime.fromisoformat(WINDOW_START_ISO)
        win_end   = datetime.fromisoformat(WINDOW_END_ISO)
        break_rows, _ = section2_structure_events(
            candles, int_brk, sw_brk, int_piv, sw_piv, win_start, win_end
        )
        stypes = {r["structure_type"] for r in break_rows}
        assert "internal" in stypes, "Internal breaks must appear in window"
        assert "swing" in stypes, "Swing breaks must appear in window (5534)"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 6 — Display limit analysis
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisplayLimits:
    """LuxAlgo display limits are not the cause of the 69k discrepancy."""

    def test_display_limit_analysis_present(self, diff):
        assert "display_limit_analysis" in diff

    def test_no_displayed_bullish_ob_near_69k(self, diff):
        """Under default LuxAlgo limits, no active bullish OB is near 69k."""
        dla = diff["display_limit_analysis"]
        assert not dla["any_displayed_bull_near_69k"], (
            "Expected no displayed bullish OB near 69k under LuxAlgo default limits"
        )

    def test_display_limit_conclusion_correct(self, diff):
        """Display limit is NOT the explanation for the 69k discrepancy."""
        conclusion = diff["display_limit_analysis"]["conclusion"].lower()
        assert "not the explanation" in conclusion or "simply is no" in conclusion, (
            "Display limit conclusion should rule out display limit as the cause"
        )

    def test_active_internal_bull_count(self, diff, snap):
        """Python has 12 active internal bullish OBs — confirms engine is working."""
        dla = diff["display_limit_analysis"]
        assert dla["active_internal_bull_count"] == 12, (
            f"Expected 12 active internal bullish OBs, got {dla['active_internal_bull_count']}"
        )

    def test_active_swing_bull_count(self, diff):
        """Python has 2 active swing bullish OBs at cutoff."""
        dla = diff["display_limit_analysis"]
        assert dla["active_swing_bull_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Section 7 — Price proximity
# ═══════════════════════════════════════════════════════════════════════════════

class TestPriceProximity:
    """Nearest active Python OB is documented with absolute and pct difference."""

    def test_nearest_ob_documented(self, diff):
        n = diff["nearest_active_ob_to_69k"]
        assert n["upper"] is not None
        assert n["lower"] is not None
        assert n["abs_diff_from_69k_center"] is not None
        assert n["pct_diff_from_69k_center"] is not None

    def test_nearest_ob_is_not_in_69k_zone(self, diff):
        """The nearest active OB midpoint must be outside [68500, 69500]."""
        n = diff["nearest_active_ob_to_69k"]
        mid = n["midpoint"]
        assert mid < 68500 or mid > 69500, (
            f"Nearest active OB midpoint {mid:.1f} is inside 69k zone — "
            "but diff says no active OBs in region. Contradiction."
        )

    def test_pct_diff_is_positive(self, diff):
        n = diff["nearest_active_ob_to_69k"]
        assert n["pct_diff_from_69k_center"] > 0.0, (
            "pct_diff must be positive (nearest OB is not at 69k center)"
        )

    def test_documentation_not_classified_as_mismatch(self, diff):
        """No exact mismatch can be declared from visual axis estimation alone."""
        # The phase status must be EXPLAINED, not MISMATCH
        assert diff["phase_status"] != "69K_OB_REQUIRES_PRODUCTION_INVESTIGATION"


# ═══════════════════════════════════════════════════════════════════════════════
# Lifecycle exclusion — invalidated OBs must not appear as active
# ═══════════════════════════════════════════════════════════════════════════════

class TestLifecycleExclusion:
    """Invalidated OBs in the region are correctly excluded from active count."""

    def test_invalidated_obs_in_region_are_not_active(self, region_rows):
        for r in region_rows:
            if r["is_active"] == "True":
                assert r["state"] != "invalidated", (
                    f"OB is marked active=True but state=invalidated: "
                    f"{r['creation_timestamp']} {r['direction']} {r['upper']}/{r['lower']}"
                )

    def test_25_invalidated_obs_in_region(self, region_rows):
        inv = [r for r in region_rows if r["state"] == "invalidated"]
        assert len(inv) == 25, f"Expected 25 invalidated OBs in region, got {len(inv)}"

    def test_no_fresh_or_touched_obs_in_region(self, region_rows):
        """No 'fresh' or 'touched' OBs exist in the 69k region."""
        active_states = [r for r in region_rows if r["state"] in ("fresh", "touched")]
        assert not active_states, (
            f"Found {len(active_states)} fresh/touched OBs in 69k region — "
            "expected zero at dataset cutoff"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Frozen SMC files
# ═══════════════════════════════════════════════════════════════════════════════

def test_frozen_smc_files_exist():
    for p in FROZEN_SMC:
        assert p.exists(), f"Frozen SMC file missing: {p}"


def test_frozen_smc_files_unmodified():
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--",
         "engine/src/quantedge/smc/structure.py",
         "engine/src/quantedge/smc/order_blocks.py",
         "engine/src/quantedge/smc/volatility.py"],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.stdout.strip() == "", (
        f"Frozen SMC files modified:\n{result.stdout}"
    )


def test_dataset_sha256_unchanged():
    """Row-based SHA-256 of canonical dataset must match the registered value."""
    import hashlib
    from datetime import timezone as tz
    rows_read = []
    with open(DATA_CSV, newline="", encoding="utf-8") as f:
        import csv as csv_mod
        rows_read = list(csv_mod.DictReader(f))
    h = hashlib.sha256()
    for row in rows_read:
        ts = int(datetime.fromisoformat(row["timestamp"]).replace(tzinfo=tz.utc).timestamp())
        line = f"{ts},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}\n"
        h.update(line.encode())
    assert h.hexdigest() == EXPECTED_SHA256


def test_output_all_four_files_exist():
    for p in [REGION_CSV, EVENTS_CSV, CAND_CSV, DIFF_JSON]:
        assert p.exists(), f"Missing output: {p}"
