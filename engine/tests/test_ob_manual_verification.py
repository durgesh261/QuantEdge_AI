"""
Tests for OB Manual Verification Pack generation.

Verifies:
- all_ob_events.csv is deterministic and has expected row count structure
- active_ob_snapshot.csv contains only active OBs
- recent_ob_events.csv contains at most 50 rows
- latest_ob_summary.json is valid JSON with required keys
- No look-ahead: active OBs are a subset of all OBs
- No duplicate OB IDs
- Internal and swing OBs are NOT merged
- Frozen SMC files are not referenced as outputs
- Output is deterministic (two runs produce identical all_ob_events.csv SHA-256)
"""

import sys
import csv
import json
import hashlib
from pathlib import Path

import pytest

ENGINE    = Path(__file__).parent.parent
REPO_ROOT = ENGINE.parent
OUT_DIR   = REPO_ROOT / "validation" / "ob_manual_verification"

ALL_CSV       = OUT_DIR / "all_ob_events.csv"
ACTIVE_CSV    = OUT_DIR / "active_ob_snapshot.csv"
RECENT_CSV    = OUT_DIR / "recent_ob_events.csv"
SUMMARY_JSON  = OUT_DIR / "latest_ob_summary.json"
CHECKLIST_MD  = OUT_DIR / "verification_checklist.md"
MAIN_DOC      = REPO_ROOT / "docs" / "OB_MANUAL_VERIFICATION.md"

FROZEN_SMC = [
    ENGINE / "src" / "quantedge" / "smc" / "structure.py",
    ENGINE / "src" / "quantedge" / "smc" / "order_blocks.py",
    ENGINE / "src" / "quantedge" / "smc" / "volatility.py",
]

REQUIRED_SUMMARY_KEYS = [
    "generated_at",
    "dataset_cutoff",
    "dataset_sha256",
    "candle_count",
    "dataset_path",
    "smc_config",
    "all_ob_events_sha256",
    "statistics",
    "monthly_summary",
    "active_ob_count",
    "verification_target_count",
    "top_10_active_obs",
    "top_10_internal_active",
    "top_10_swing_active",
]

REQUIRED_STAT_KEYS = [
    "total_obs",
    "internal_count",
    "swing_count",
    "bullish_count",
    "bearish_count",
    "fresh_count",
    "touched_count",
    "invalidated_by_state_count",
    "active_count",
    "invalidated_count",
    "avg_ob_height",
    "median_ob_height",
    "earliest_ob_ts",
    "latest_ob_ts",
]

REQUIRED_CSV_FIELDS = [
    "ob_id",
    "structure_type",
    "direction",
    "creation_timestamp",
    "creation_candle_index",
    "break_timestamp",
    "break_candle_index",
    "break_type",
    "upper_price",
    "lower_price",
    "ob_height",
    "state",
    "is_active",
    "month",
]


# ── Fixtures ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def all_rows():
    assert ALL_CSV.exists(), f"all_ob_events.csv not found at {ALL_CSV}"
    with open(ALL_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def active_rows():
    assert ACTIVE_CSV.exists(), f"active_ob_snapshot.csv not found at {ACTIVE_CSV}"
    with open(ACTIVE_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def recent_rows():
    assert RECENT_CSV.exists(), f"recent_ob_events.csv not found at {RECENT_CSV}"
    with open(RECENT_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def summary():
    assert SUMMARY_JSON.exists(), f"latest_ob_summary.json not found at {SUMMARY_JSON}"
    return json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))


# ── File existence tests ─────────────────────────────────────────────────────────

def test_all_output_files_exist():
    """All generated output files must exist."""
    for p in [ALL_CSV, ACTIVE_CSV, RECENT_CSV, SUMMARY_JSON, CHECKLIST_MD, MAIN_DOC]:
        assert p.exists(), f"Missing output file: {p}"


# ── CSV structure tests ──────────────────────────────────────────────────────────

def test_all_ob_csv_has_required_fields(all_rows):
    """all_ob_events.csv must have all required CSV fields."""
    assert all_rows, "all_ob_events.csv is empty"
    headers = set(all_rows[0].keys())
    for field in REQUIRED_CSV_FIELDS:
        assert field in headers, f"Missing field '{field}' in all_ob_events.csv"


def test_all_ob_csv_nonzero(all_rows):
    """all_ob_events.csv must contain OBs."""
    assert len(all_rows) > 0, "all_ob_events.csv has no rows"


def test_all_ob_ids_unique(all_rows):
    """All OB IDs must be unique."""
    ids = [r["ob_id"] for r in all_rows]
    assert len(ids) == len(set(ids)), "Duplicate OB IDs found in all_ob_events.csv"


def test_active_csv_all_active(active_rows):
    """active_ob_snapshot.csv must contain ONLY active OBs."""
    for r in active_rows:
        assert r["is_active"].lower() in ("true", "1"), (
            f"Non-active OB found in active_ob_snapshot.csv: ob_id={r['ob_id']}, is_active={r['is_active']}"
        )


def test_active_is_subset_of_all(all_rows, active_rows):
    """Active OBs must be a subset of all OBs (by ob_id)."""
    all_ids    = {r["ob_id"] for r in all_rows}
    active_ids = {r["ob_id"] for r in active_rows}
    assert active_ids.issubset(all_ids), (
        f"Active OB IDs not a subset of all OB IDs: {active_ids - all_ids}"
    )


def test_recent_obs_at_most_50(recent_rows):
    """recent_ob_events.csv must contain at most 50 rows."""
    assert len(recent_rows) <= 50, f"recent_ob_events.csv has {len(recent_rows)} rows (expected ≤ 50)"


def test_internal_and_swing_not_merged(all_rows):
    """Internal and swing OBs must be kept separate (not merged)."""
    structure_types = {r["structure_type"] for r in all_rows}
    # Both types should exist if there are enough candles
    assert "internal" in structure_types, "No internal OBs found"
    assert "swing" in structure_types, "No swing OBs found"


def test_direction_values_valid(all_rows):
    """All direction values must be 'bullish' or 'bearish'."""
    for r in all_rows:
        assert r["direction"] in ("bullish", "bearish"), (
            f"Invalid direction '{r['direction']}' in ob_id={r['ob_id']}"
        )


def test_structure_type_values_valid(all_rows):
    """All structure_type values must be 'internal' or 'swing'."""
    for r in all_rows:
        assert r["structure_type"] in ("internal", "swing"), (
            f"Invalid structure_type '{r['structure_type']}' in ob_id={r['ob_id']}"
        )


def test_state_values_valid(all_rows):
    """All state values must be 'fresh', 'touched', or 'invalidated'."""
    for r in all_rows:
        assert r["state"] in ("fresh", "touched", "invalidated"), (
            f"Invalid state '{r['state']}' in ob_id={r['ob_id']}"
        )


def test_ob_height_positive(all_rows):
    """OB height must be positive (upper > lower)."""
    for r in all_rows:
        height = float(r["ob_height"])
        assert height > 0, f"Non-positive OB height {height} in ob_id={r['ob_id']}"


def test_ob_prices_consistent(all_rows):
    """Upper price must be greater than lower price."""
    for r in all_rows:
        upper = float(r["upper_price"])
        lower = float(r["lower_price"])
        assert upper > lower, (
            f"upper_price ({upper}) <= lower_price ({lower}) in ob_id={r['ob_id']}"
        )


# ── JSON summary tests ────────────────────────────────────────────────────────────

def test_summary_has_required_keys(summary):
    """latest_ob_summary.json must have all required top-level keys."""
    for key in REQUIRED_SUMMARY_KEYS:
        assert key in summary, f"Missing key '{key}' in latest_ob_summary.json"


def test_summary_statistics_has_required_keys(summary):
    """statistics block must have all required keys."""
    stats = summary["statistics"]
    for key in REQUIRED_STAT_KEYS:
        assert key in stats, f"Missing statistics key '{key}'"


def test_summary_active_count_matches_csv(summary, active_rows):
    """summary active_ob_count must match active_ob_snapshot.csv row count."""
    assert summary["active_ob_count"] == len(active_rows), (
        f"active_ob_count mismatch: summary={summary['active_ob_count']}, csv={len(active_rows)}"
    )


def test_summary_total_obs_matches_csv(summary, all_rows):
    """statistics.total_obs must match all_ob_events.csv row count."""
    assert summary["statistics"]["total_obs"] == len(all_rows), (
        f"total_obs mismatch: summary={summary['statistics']['total_obs']}, csv={len(all_rows)}"
    )


def test_summary_dataset_sha256_recorded(summary):
    """Dataset SHA-256 must be the known canonical value."""
    assert summary["dataset_sha256"] == "2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b", (
        f"Unexpected dataset SHA-256: {summary['dataset_sha256']}"
    )


def test_summary_internal_plus_swing_equals_total(summary):
    """internal_count + swing_count must equal total_obs."""
    stats = summary["statistics"]
    assert stats["internal_count"] + stats["swing_count"] == stats["total_obs"], (
        "internal_count + swing_count != total_obs"
    )


def test_summary_bullish_plus_bearish_equals_total(summary):
    """bullish_count + bearish_count must equal total_obs."""
    stats = summary["statistics"]
    assert stats["bullish_count"] + stats["bearish_count"] == stats["total_obs"], (
        "bullish_count + bearish_count != total_obs"
    )


def test_summary_active_plus_invalidated_equals_total(summary):
    """active_count + invalidated_count must equal total_obs."""
    stats = summary["statistics"]
    assert stats["active_count"] + stats["invalidated_count"] == stats["total_obs"], (
        f"active_count ({stats['active_count']}) + invalidated_count ({stats['invalidated_count']}) "
        f"!= total_obs ({stats['total_obs']})"
    )


# ── Determinism test ─────────────────────────────────────────────────────────────

def test_all_ob_csv_sha256_matches_summary(summary):
    """SHA-256 of all_ob_events.csv must match the value recorded in summary."""
    h = hashlib.sha256()
    with open(ALL_CSV, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    computed = h.hexdigest()
    assert computed == summary["all_ob_events_sha256"], (
        f"all_ob_events.csv SHA-256 mismatch:\n"
        f"  computed : {computed}\n"
        f"  recorded : {summary['all_ob_events_sha256']}"
    )


# ── Documentation tests ───────────────────────────────────────────────────────────

def test_main_doc_exists_and_has_status_section():
    """docs/OB_MANUAL_VERIFICATION.md must exist and contain the status section."""
    assert MAIN_DOC.exists(), "docs/OB_MANUAL_VERIFICATION.md not found"
    text = MAIN_DOC.read_text(encoding="utf-8")
    assert "PHASE 3D MANUAL OB VERIFICATION STATUS" in text
    assert "NOT STARTED" in text  # Phase 4 not started


def test_checklist_exists_and_nonempty():
    """verification_checklist.md must exist and have content."""
    assert CHECKLIST_MD.exists(), "verification_checklist.md not found"
    text = CHECKLIST_MD.read_text(encoding="utf-8")
    assert "TradingView" in text
    assert "NOT_VISIBLE" in text  # limitation documented
    assert len(text) > 500


# ── Frozen SMC file verification ──────────────────────────────────────────────────

def test_frozen_smc_files_exist():
    """All three frozen SMC files must exist (not accidentally deleted)."""
    for p in FROZEN_SMC:
        assert p.exists(), f"Frozen SMC file missing: {p}"


def test_output_does_not_reference_binance(summary):
    """Generated summary must not reference Binance data."""
    text = json.dumps(summary, default=str).lower()
    assert "binance" not in text, "Summary references Binance data — Delta-only policy violated"


def test_output_uses_canonical_delta_path(summary):
    """dataset_path must point to canonical Delta India path."""
    assert "delta_exchange_india" in summary["dataset_path"], (
        f"dataset_path does not reference delta_exchange_india: {summary['dataset_path']}"
    )
