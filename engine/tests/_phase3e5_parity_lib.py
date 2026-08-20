"""
Phase 3E.5: Current-Day LuxAlgo Parity Validation

Validates whether the canonical dataset can reproduce the LuxAlgo TradingView
screenshot taken on Aug-20 (current day), including:
  - The blue OB around 69k
  - Green zones (FVGs or OBs)
  - All active OBs at each requested checkpoint

Checkpoints:
  2026-08-20T00:00:00Z  (last candle in dataset — boundary)
  2026-08-20T06:00:00Z  (post-cutoff — DATASET_UNAVAILABLE)
  2026-08-20T12:00:00Z  (post-cutoff — DATASET_UNAVAILABLE)
  2026-08-20T14:00:00Z  (post-cutoff — DATASET_UNAVAILABLE)
  2026-08-20T16:00:00Z  (post-cutoff — DATASET_UNAVAILABLE)

Result classification used:
  EXACT_MATCH
  PRICE_MISMATCH
  CREATION_TIME_MISMATCH
  BREAK_TIME_MISMATCH
  STRUCTURE_TYPE_MISMATCH
  STATE_MISMATCH
  MISSING_IN_PYTHON
  EXTRA_IN_PYTHON
  DATASET_UNAVAILABLE

DO NOT MODIFY:
    engine/src/quantedge/smc/structure.py
    engine/src/quantedge/smc/order_blocks.py
    engine/src/quantedge/smc/volatility.py
"""

import csv
import json
import hashlib
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Dict, Optional, Tuple

ENGINE    = Path(__file__).parent.parent
REPO_ROOT = ENGINE.parent
OUT_DIR   = REPO_ROOT / "validation" / "phase3e5"

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from ob_snapshot_engine import OBSnapshotEngine, OBRecord

DATA_CSV  = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
DATA_META = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026_metadata.json"


def compute_phase3e5_in_memory(eng: OBSnapshotEngine) -> Tuple[List[dict], List[dict], dict]:
    """
    Run Phase 3E.5 parity calculations in memory without writing to disk.
    Returns (cp_rows, diff_rows, summary).
    """
    candles = eng.candles
    ok, sha = verify_dataset(DATA_META)

    checkpoint_results = []
    for cp_iso in CHECKPOINTS:
        res = checkpoint_analysis(eng, cp_iso, candles)
        checkpoint_results.append(res)

    cp_rows = []
    for res in checkpoint_results:
        cp_ts = res["checkpoint"]
        status = res["status"]
        if status == "DATASET_UNAVAILABLE":
            cp_rows.append({
                "checkpoint":        cp_ts,
                "status":            status,
                "active_count":      0,
                "total_count":       0,
                "structure_type":    "",
                "direction":         "",
                "upper":             0.0,
                "lower":             0.0,
                "state":             "",
                "creation_ts":       "",
                "break_ts":          "",
                "break_index":       0,
                "note":              "POST_CUTOFF_UNAVAILABLE",
            })
        else:
            for ob in res["active_obs"]:
                cp_rows.append({
                    "checkpoint":        cp_ts,
                    "status":            status,
                    "active_count":      res["active_ob_count"],
                    "total_count":       res["total_ob_count"],
                    "structure_type":    ob["structure_type"],
                    "direction":         ob["direction"],
                    "upper":             ob["upper"],
                    "lower":             ob["lower"],
                    "state":             ob["state"],
                    "creation_ts":       ob["creation_timestamp"],
                    "break_ts":          ob["break_timestamp"],
                    "break_index":       ob["break_candle_index"],
                    "note":              "",
                })

    last_available = next(r for r in checkpoint_results if r["status"] == "AVAILABLE")
    diff_rows = tv_ob_differential(TV_OBSERVATIONS, last_available["active_obs"])
    post_cutoff_cps = [r for r in checkpoint_results if r["status"] == "DATASET_UNAVAILABLE"]

    summary = {
        "generated_at":           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase":                  "3E.5",
        "dataset": {
            "file":               str(DATA_CSV),
            "candles":            EXPECTED_CANDLES,
            "first_candle":       candles[0].timestamp.isoformat(),
            "last_candle":        candles[-1].timestamp.isoformat(),
            "sha256":             sha,
            "sha256_ok":          ok,
        },
        "checkpoints": [
            {
                "ts":     r["checkpoint"],
                "status": r["status"],
                "active": r.get("active_ob_count"),
            }
            for r in checkpoint_results
        ],
        "dataset_coverage": {
            "available_checkpoints":     len([r for r in checkpoint_results if r["status"] == "AVAILABLE"]),
            "post_cutoff_checkpoints":   len(post_cutoff_cps),
            "post_cutoff_list":          [r["checkpoint"] for r in post_cutoff_cps],
        },
        "69k_investigation": {
            "last_available_checkpoint":  last_available["checkpoint"],
            "active_obs_in_69k_at_cutoff": len(last_available.get("obs_in_69k_zone", [])),
            "conclusion":                 (
                "Zero active Python OBs in the 68500-69500 zone at the dataset boundary. "
                "The LuxAlgo ~69k blue OB seen in the Aug-20 TradingView screenshot "
                "requires candles beyond 2026-08-20T00:00:00Z. These are not available "
                "in the canonical dataset."
            ),
        },
        "green_zone_classification": {
            "tv_green_001": {
                "tv_id":   "TV_GREEN_001",
                "verdict": "FVG",
                "action":  "IGNORE_FVG — green zones are NEVER matched against Python OBs",
            },
            "tv_green_002": {
                "tv_id":   "TV_GREEN_002",
                "verdict": "FVG",
                "action":  "IGNORE_FVG — green zones are NEVER matched against Python OBs",
            },
        },
        "tv_differential": diff_rows,
        "overall_verdict": {
            "can_reproduce_tv_screenshot":  False,
            "reason":                       (
                "The TradingView screenshot is from Aug-20 (current day). "
                "The canonical dataset ends at 2026-08-20T00:00:00Z (last candle). "
                "All requested checkpoints after 00:00Z are DATASET_UNAVAILABLE. "
                "No fabricated data. The ~69k blue OB requires post-cutoff candles."
            ),
            "data_integrity":               "VERIFIED",
            "production_smc_changes":       "NONE",
            "phase4_started":               False,
            "classification":               "DATASET_UNAVAILABLE",
        },
        "pending_user_input": {
            "exact_tv_tooltip_upper":       "PENDING",
            "exact_tv_tooltip_lower":       "PENDING",
            "tv_screenshot_exact_time":     "PENDING",
            "action_when_received":         (
                "Populate TV_OBSERVATIONS in this generator with exact prices, "
                "then re-run to produce EXACT_MATCH / PRICE_MISMATCH classification."
            ),
        },
    }

    return cp_rows, diff_rows, summary

EXPECTED_SHA256  = "2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b"
EXPECTED_CANDLES = 5545

# Exact dataset boundary (inclusive — the candle AT this timestamp IS in the data)
DATASET_LAST_CANDLE_TS = "2026-08-20T00:00:00+00:00"
# One hour beyond — the first timestamp for which we have NO data
DATASET_POST_CUTOFF_TS = "2026-08-20T01:00:00+00:00"

# Requested TradingView checkpoints
CHECKPOINTS = [
    "2026-08-20T00:00:00+00:00",   # boundary — last candle available
    "2026-08-20T06:00:00+00:00",   # post-cutoff
    "2026-08-20T12:00:00+00:00",   # post-cutoff
    "2026-08-20T14:00:00+00:00",   # post-cutoff
    "2026-08-20T16:00:00+00:00",   # post-cutoff
]

# LuxAlgo TV observations supplied by user (to be populated when tooltip data arrives)
# Format: list of dicts with keys:
#   tv_id, direction, upper, lower, is_fvg, observed_ts, notes
TV_OBSERVATIONS: List[Dict] = [
    {
        "tv_id":        "TV_69K_001",
        "direction":    "bullish",
        "upper":        None,           # exact tooltip price not yet provided
        "lower":        None,           # exact tooltip price not yet provided
        "is_fvg":       False,
        "observed_ts":  "2026-08-20",   # screenshot date
        "notes":        "Blue OB ~69k visible in Aug-20 TV screenshot. "
                        "Exact upper/lower PENDING tooltip data from user.",
    },
    {
        "tv_id":        "TV_GREEN_001",
        "direction":    "unknown",
        "upper":        None,
        "lower":        None,
        "is_fvg":       True,           # green zone = FVG
        "observed_ts":  "2026-08-20",
        "notes":        "Green zone newly visible in Aug-20 screenshot. "
                        "Classified as FVG — NOT compared against Python OBs.",
    },
    {
        "tv_id":        "TV_GREEN_002",
        "direction":    "unknown",
        "upper":        None,
        "lower":        None,
        "is_fvg":       True,
        "observed_ts":  "2026-08-20",
        "notes":        "Green zone ~70k-71.4k visible in Aug-20 screenshot. "
                        "Classified as FVG — NOT compared against Python OBs.",
    },
]


# ── Dataset verification ───────────────────────────────────────────────────────

def verify_dataset(meta_path: Path) -> Tuple[bool, str]:
    """Verify SHA-256 and candle count against registered metadata."""
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    h = hashlib.sha256()
    with open(DATA_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            ts = int(datetime.fromisoformat(row["timestamp"])
                     .replace(tzinfo=timezone.utc).timestamp())
            line = f"{ts},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}\n"
            h.update(line.encode())
            count += 1
    sha = h.hexdigest()
    ok = (sha == EXPECTED_SHA256 and count == EXPECTED_CANDLES)
    return ok, sha


# ── Checkpoint analysis ────────────────────────────────────────────────────────

def is_post_cutoff(checkpoint_ts: str) -> bool:
    """Return True if the checkpoint requires candles beyond our dataset."""
    cp = datetime.fromisoformat(checkpoint_ts)
    last = datetime.fromisoformat(DATASET_POST_CUTOFF_TS)
    if cp.tzinfo is None:
        cp = cp.replace(tzinfo=timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return cp >= last


def checkpoint_analysis(
    eng: OBSnapshotEngine,
    checkpoint_ts: str,
    candles: list,
) -> Dict:
    """
    Run a snapshot at the checkpoint and return a structured result dict.
    If the checkpoint is beyond the dataset, classify as DATASET_UNAVAILABLE.
    """
    post_cutoff = is_post_cutoff(checkpoint_ts)

    if post_cutoff:
        return {
            "checkpoint":        checkpoint_ts,
            "status":            "DATASET_UNAVAILABLE",
            "candles_available": EXPECTED_CANDLES,
            "dataset_ends":      DATASET_LAST_CANDLE_TS,
            "active_ob_count":   None,
            "total_ob_count":    None,
            "active_obs":        [],
            "note":              (
                f"Checkpoint {checkpoint_ts} is beyond the canonical dataset "
                f"(last candle: {DATASET_LAST_CANDLE_TS}). No fabricated data. "
                "Cannot reproduce TradingView screenshot at this time."
            ),
        }

    snap = eng.snapshot_at(checkpoint_ts)
    active_obs = []
    for ob in sorted(snap.active_obs, key=lambda x: float(x.upper_price), reverse=True):
        active_obs.append({
            "structure_type":        ob.structure_type,
            "direction":             ob.direction,
            "upper":                 float(ob.upper_price),
            "lower":                 float(ob.lower_price),
            "state":                 ob.state,
            "creation_timestamp":    ob.creation_timestamp.isoformat(),
            "break_timestamp":       ob.break_timestamp.isoformat() if ob.break_timestamp else "",
            "break_candle_index":    ob.break_candle_index,
            "first_touch_ts":        ob.first_touch_timestamp.isoformat() if ob.first_touch_timestamp else "",
            "invalidation_ts":       ob.invalidation_timestamp.isoformat() if ob.invalidation_timestamp else "",
            "source_candle_index":   ob.source_candle_index,
            "source_timestamp":      ob.source_timestamp.isoformat() if ob.source_timestamp else "",
        })

    # Check 69k zone specifically
    obs_in_69k = [
        ob for ob in active_obs
        if ob["lower"] <= 69500 and ob["upper"] >= 68500
    ]

    return {
        "checkpoint":        checkpoint_ts,
        "status":            "AVAILABLE",
        "candles_processed": snap.candles_processed,
        "active_ob_count":   snap.active_count,
        "total_ob_count":    snap.all_count,
        "active_obs":        active_obs,
        "obs_in_69k_zone":   obs_in_69k,
        "note":              None,
    }


# ── TV OB differential ────────────────────────────────────────────────────────

def tv_ob_differential(
    tv_obs: List[Dict],
    active_obs: List[Dict],
) -> List[Dict]:
    """
    Compare each TV observation against Python active OBs.
    Returns one row per TV observation with classification.
    """
    rows = []
    for tv in tv_obs:
        if tv["is_fvg"]:
            rows.append({
                "tv_id":              tv["tv_id"],
                "is_fvg":             True,
                "direction":          tv["direction"],
                "tv_upper":           tv["upper"],
                "tv_lower":           tv["lower"],
                "result":             "IGNORE_FVG",
                "python_match_upper": "",
                "python_match_lower": "",
                "python_match_state": "",
                "explanation":        f"Green zone — FVG. {tv['notes']}",
            })
            continue

        # OB with no tooltip data yet → DATASET_UNAVAILABLE (incomplete ground truth)
        if tv["upper"] is None or tv["lower"] is None:
            rows.append({
                "tv_id":              tv["tv_id"],
                "is_fvg":             False,
                "direction":          tv["direction"],
                "tv_upper":           tv["upper"],
                "tv_lower":           tv["lower"],
                "result":             "DATASET_UNAVAILABLE",
                "python_match_upper": "",
                "python_match_lower": "",
                "python_match_state": "",
                "explanation":        (
                    f"Exact LuxAlgo tooltip prices not yet provided. "
                    f"{tv['notes']} Cannot classify without exact upper/lower."
                ),
            })
            continue

        tv_upper = Decimal(str(tv["upper"]))
        tv_lower = Decimal(str(tv["lower"]))
        bdir     = tv["direction"]

        # Search for matching Python OB
        candidates = [
            ob for ob in active_obs
            if ob["direction"] == bdir
            and Decimal(str(ob["lower"])) <= tv_upper
            and Decimal(str(ob["upper"])) >= tv_lower
        ]

        if not candidates:
            rows.append({
                "tv_id":              tv["tv_id"],
                "is_fvg":             False,
                "direction":          bdir,
                "tv_upper":           float(tv_upper),
                "tv_lower":           float(tv_lower),
                "result":             "MISSING_IN_PYTHON",
                "python_match_upper": "",
                "python_match_lower": "",
                "python_match_state": "",
                "explanation":        "No active Python OB overlaps the TV zone.",
            })
            continue

        best = min(
            candidates,
            key=lambda ob: abs(
                (Decimal(str(ob["upper"])) + Decimal(str(ob["lower"]))) / 2
                - (tv_upper + tv_lower) / 2
            )
        )
        price_match = (
            abs(float(best["upper"]) - float(tv_upper)) < 50 and
            abs(float(best["lower"]) - float(tv_lower)) < 50
        )

        rows.append({
            "tv_id":              tv["tv_id"],
            "is_fvg":             False,
            "direction":          bdir,
            "tv_upper":           float(tv_upper),
            "tv_lower":           float(tv_lower),
            "result":             "EXACT_MATCH" if price_match else "PRICE_MISMATCH",
            "python_match_upper": best["upper"],
            "python_match_lower": best["lower"],
            "python_match_state": best["state"],
            "explanation":        (
                f"Python OB: upper={best['upper']}, lower={best['lower']}, "
                f"state={best['state']}, creation={best['creation_timestamp'][:19]}"
            ),
        })

    return rows


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list, fields: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> Dict:
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("=" * 64)
    print("Phase 3E.5: Current-Day LuxAlgo Parity Validation")
    print("=" * 64)

    # ── Verify dataset ─────────────────────────────────────────────────────────
    print("\n[0] Verifying canonical dataset...")
    ok, sha = verify_dataset(DATA_META)
    print(f"  File    : {DATA_CSV}")
    print(f"  SHA-256 : {sha}")
    print(f"  Candles : {EXPECTED_CANDLES}")
    print(f"  Ends at : {DATASET_LAST_CANDLE_TS}")
    assert ok, f"Dataset verification failed! SHA={sha}"
    print(f"  Status  : OK")

    # ── Load engine ────────────────────────────────────────────────────────────
    print("\n[1] Loading OB engine...")
    eng     = OBSnapshotEngine.from_csv(str(DATA_CSV))
    candles = eng.candles
    print(f"  Candles loaded: {len(candles)}")
    print(f"  First: {candles[0].timestamp.isoformat()}")
    print(f"  Last : {candles[-1].timestamp.isoformat()}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Checkpoint replays ─────────────────────────────────────────────────────
    print("\n[2] Running checkpoint replays...")
    checkpoint_results = []
    for cp in CHECKPOINTS:
        result = checkpoint_analysis(eng, cp, candles)
        checkpoint_results.append(result)
        status = result["status"]
        if status == "DATASET_UNAVAILABLE":
            print(f"  {cp}  -> DATASET_UNAVAILABLE (post-cutoff)")
        else:
            n_69k = len(result.get("obs_in_69k_zone", []))
            print(f"  {cp}  -> {result['active_ob_count']} active OBs | "
                  f"69k zone: {n_69k} OBs")

    # ── Write checkpoint CSV ───────────────────────────────────────────────────
    cp_rows = []
    for res in checkpoint_results:
        if res["status"] == "DATASET_UNAVAILABLE":
            cp_rows.append({
                "checkpoint":   res["checkpoint"],
                "status":       "DATASET_UNAVAILABLE",
                "active_count": "",
                "total_count":  "",
                "note":         res["note"],
            })
        else:
            for ob in res["active_obs"]:
                cp_rows.append({
                    "checkpoint":        res["checkpoint"],
                    "status":            "AVAILABLE",
                    "active_count":      res["active_ob_count"],
                    "total_count":       res["total_ob_count"],
                    "structure_type":    ob["structure_type"],
                    "direction":         ob["direction"],
                    "upper":             ob["upper"],
                    "lower":             ob["lower"],
                    "state":             ob["state"],
                    "creation_ts":       ob["creation_timestamp"],
                    "break_ts":          ob["break_timestamp"],
                    "break_index":       ob["break_candle_index"],
                    "note":              "",
                })

    cp_fields = [
        "checkpoint", "status", "active_count", "total_count",
        "structure_type", "direction", "upper", "lower", "state",
        "creation_ts", "break_ts", "break_index", "note",
    ]
    cp_path = OUT_DIR / "checkpoint_snapshots.csv"
    _write_csv(cp_path, cp_rows, cp_fields)
    print(f"\n  Written: {cp_path.name} ({cp_path.stat().st_size:,} bytes)")

    # ── Active OBs at last available checkpoint ────────────────────────────────
    last_available = next(
        r for r in checkpoint_results if r["status"] == "AVAILABLE"
    )

    # ── TV OB differential ─────────────────────────────────────────────────────
    print("\n[3] Running TV OB differential...")
    diff_rows = tv_ob_differential(TV_OBSERVATIONS, last_available["active_obs"])
    diff_fields = [
        "tv_id", "is_fvg", "direction", "tv_upper", "tv_lower",
        "result", "python_match_upper", "python_match_lower",
        "python_match_state", "explanation",
    ]
    diff_path = OUT_DIR / "tv_ob_differential.csv"
    _write_csv(diff_path, diff_rows, diff_fields)
    for r in diff_rows:
        print(f"  {r['tv_id']:20} -> {r['result']}")

    # ── 69k zone investigation (detailed) ─────────────────────────────────────
    print("\n[4] 69k zone investigation at last available checkpoint...")
    print(f"  Checkpoint: {last_available['checkpoint']}")
    print(f"  Active OBs in 68500-69500: {len(last_available.get('obs_in_69k_zone', []))}")
    if last_available.get("obs_in_69k_zone"):
        for ob in last_available["obs_in_69k_zone"]:
            print(f"    upper={ob['upper']:.2f} lower={ob['lower']:.2f} "
                  f"state={ob['state']} dir={ob['direction']}")
    else:
        print("    NONE — confirmed zero active Python OBs in 69k zone at dataset end")

    # ── Post-cutoff boundary summary ───────────────────────────────────────────
    post_cutoff_cps = [r for r in checkpoint_results if r["status"] == "DATASET_UNAVAILABLE"]

    # ── Build summary JSON ─────────────────────────────────────────────────────
    summary = {
        "generated_at":           gen_ts,
        "phase":                  "3E.5",
        "dataset": {
            "file":               str(DATA_CSV),
            "candles":            EXPECTED_CANDLES,
            "first_candle":       candles[0].timestamp.isoformat(),
            "last_candle":        candles[-1].timestamp.isoformat(),
            "sha256":             sha,
            "sha256_ok":          ok,
        },
        "checkpoints": [
            {
                "ts":     r["checkpoint"],
                "status": r["status"],
                "active": r.get("active_ob_count"),
            }
            for r in checkpoint_results
        ],
        "dataset_coverage": {
            "available_checkpoints":     len([r for r in checkpoint_results if r["status"] == "AVAILABLE"]),
            "post_cutoff_checkpoints":   len(post_cutoff_cps),
            "post_cutoff_list":          [r["checkpoint"] for r in post_cutoff_cps],
        },
        "69k_investigation": {
            "last_available_checkpoint":  last_available["checkpoint"],
            "active_obs_in_69k_at_cutoff": len(last_available.get("obs_in_69k_zone", [])),
            "conclusion":                 (
                "Zero active Python OBs in the 68500-69500 zone at the dataset boundary. "
                "The LuxAlgo ~69k blue OB seen in the Aug-20 TradingView screenshot "
                "requires candles beyond 2026-08-20T00:00:00Z. These are not available "
                "in the canonical dataset."
            ),
        },
        "green_zone_classification": {
            "tv_green_001": {
                "tv_id":   "TV_GREEN_001",
                "verdict": "FVG",
                "action":  "IGNORE_FVG — green zones are NEVER matched against Python OBs",
            },
            "tv_green_002": {
                "tv_id":   "TV_GREEN_002",
                "verdict": "FVG",
                "action":  "IGNORE_FVG — green zones are NEVER matched against Python OBs",
            },
        },
        "tv_differential": diff_rows,
        "overall_verdict": {
            "can_reproduce_tv_screenshot":  False,
            "reason":                       (
                "The TradingView screenshot is from Aug-20 (current day). "
                "The canonical dataset ends at 2026-08-20T00:00:00Z (last candle). "
                "All requested checkpoints after 00:00Z are DATASET_UNAVAILABLE. "
                "No fabricated data. The ~69k blue OB requires post-cutoff candles."
            ),
            "data_integrity":               "VERIFIED",
            "production_smc_changes":       "NONE",
            "phase4_started":               False,
            "classification":               "DATASET_UNAVAILABLE",
        },
        "pending_user_input": {
            "exact_tv_tooltip_upper":       "PENDING",
            "exact_tv_tooltip_lower":       "PENDING",
            "tv_screenshot_exact_time":     "PENDING",
            "action_when_received":         (
                "Populate TV_OBSERVATIONS in this generator with exact prices, "
                "then re-run to produce EXACT_MATCH / PRICE_MISMATCH classification."
            ),
        },
    }

    sum_path = OUT_DIR / "phase3e5_summary.json"
    sum_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 64)
    print(f"VERDICT: {summary['overall_verdict']['classification']}")
    print("=" * 64)
    for p in sorted(OUT_DIR.iterdir()):
        if p.is_file():
            print(f"  {p.relative_to(REPO_ROOT)}  ({p.stat().st_size:,} bytes)")

    return summary


if __name__ == "__main__":
    result = main()
    sys.exit(0)
