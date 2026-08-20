"""
Phase 3D: Generate Python OB snapshots at the 5 selected validation windows.

This script:
1. Loads the exact Delta India BTCUSD 1H dataset
2. Runs causal OB snapshot at each selected timestamp
3. Saves Python-side OB inventory as JSON
4. Creates empty TradingView reference files for user to fill in

Run from repo root:
    python engine/generate_3d_snapshots.py
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone

ENGINE = Path(__file__).parent
sys.path.insert(0, str(ENGINE / "src"))

from ob_snapshot_engine import OBSnapshotEngine, OBRecord

REPO      = ENGINE.parent
DATA_CSV  = ENGINE / "data" / "historical" / "BTCUSD.P" / "1h" / "2026_delta_india.csv"
DATA_META = ENGINE / "data" / "historical" / "BTCUSD.P" / "1h" / "2026_delta_india_metadata.json"

OUT_ROOT  = REPO / "validation" / "tradingview_ob_reference"
OUT_ROOT.mkdir(parents=True, exist_ok=True)

# ── 5 Snapshot Windows ──────────────────────────────────────────────────────────
# Selected to cover:
#   - bearish OB creation, bullish OB creation
#   - OB touched but alive, OB mitigated, multiple simultaneous OBs
#   - internal OB, swing OB
#
# Each snapshot timestamp is a bar-close of a specific 1H candle.
# TradingView: navigate to this exact candle and read all visible OB zones.
SNAPSHOTS = [
    {
        "id": "S1",
        "timestamp": "2026-02-10T00:00:00+00:00",
        "category": "bearish_trend",
        "description": "End of Jan-Feb 2026 BTC correction — bearish OBs active",
        "tv_instruction": (
            "Navigate to BTCUSD.P 1H chart at Delta Exchange India. "
            "Go to date: 2026-02-10 00:00 UTC (2026-02-10 05:30 IST). "
            "Record ALL LuxAlgo OB boxes visible on the chart at this candle. "
            "For each box: direction (blue=bullish, red=bearish), approximate top/bottom price, "
            "whether internal or swing, and whether the label shows it as fresh/touched."
        ),
    },
    {
        "id": "S2",
        "timestamp": "2026-04-01T00:00:00+00:00",
        "category": "bullish_trend",
        "description": "End of Mar-Apr 2026 recovery rally — bullish OBs active",
        "tv_instruction": (
            "Navigate to BTCUSD.P 1H chart. "
            "Go to date: 2026-04-01 00:00 UTC (2026-04-01 05:30 IST). "
            "Record ALL visible LuxAlgo OB boxes: direction, top/bottom price, internal/swing, state."
        ),
    },
    {
        "id": "S3",
        "timestamp": "2026-05-20T00:00:00+00:00",
        "category": "ranging_consolidation",
        "description": "May 2026 consolidation — mixed OBs, some touched but alive",
        "tv_instruction": (
            "Navigate to BTCUSD.P 1H chart. "
            "Go to date: 2026-05-20 00:00 UTC (2026-05-20 05:30 IST). "
            "Record ALL visible LuxAlgo OB boxes. Pay special attention to boxes "
            "that appear 'lighter' or 'faded' — these may be touched (visited but not broken)."
        ),
    },
    {
        "id": "S4",
        "timestamp": "2026-07-31T14:00:00+00:00",
        "category": "bearish_swing_bos",
        "description": "Jul 31 2026 — swing-level BOS bearish, multiple OBs active",
        "tv_instruction": (
            "Navigate to BTCUSD.P 1H chart. "
            "Go to date: 2026-07-31 14:00 UTC (2026-07-31 19:30 IST). "
            "This is near the swing bearish BOS event. "
            "Record ALL visible LuxAlgo OB boxes. This window should show both "
            "bearish supply OBs above and any bullish demand OBs below price."
        ),
    },
    {
        "id": "S5",
        "timestamp": "2026-08-19T14:00:00+00:00",
        "category": "bullish_swing_choch",
        "description": "Aug 19 2026 — swing CHOCH bullish, fresh bullish OBs forming",
        "tv_instruction": (
            "Navigate to BTCUSD.P 1H chart. "
            "Go to date: 2026-08-19 14:00 UTC (2026-08-19 19:30 IST). "
            "This is at the swing bullish CHOCH event. "
            "Record ALL visible LuxAlgo OB boxes, especially new green/bullish boxes "
            "that appear after the trend flip. Record their exact top and bottom prices "
            "by hovering over each box."
        ),
    },
]


def ob_record_to_py_entry(r: OBRecord) -> dict:
    return {
        "structure_type":        r.structure_type,
        "direction":             r.direction,
        "creation_timestamp":    r.creation_timestamp.isoformat(),
        "creation_candle_index": r.creation_candle_index,
        "break_timestamp":       r.break_timestamp.isoformat() if r.break_timestamp else None,
        "break_candle_index":    r.break_candle_index,
        "break_type":            r.break_type,
        "source_timestamp":      r.source_timestamp.isoformat(),
        "upper_price":           float(r.upper_price),
        "lower_price":           float(r.lower_price),
        "state":                 r.state,
        "first_touch_timestamp": r.first_touch_timestamp.isoformat() if r.first_touch_timestamp else None,
        "invalidation_timestamp":r.invalidation_timestamp.isoformat() if r.invalidation_timestamp else None,
        "pivot_index":           r.pivot_index,
        "pivot_timestamp":       r.pivot_timestamp.isoformat() if r.pivot_timestamp else None,
        "pivot_price":           float(r.pivot_price) if r.pivot_price else None,
        "is_active":             r.is_active,
    }


def build_tv_reference_template(snap_meta: dict, active_count: int) -> dict:
    """Build an empty TradingView reference template for user to fill in."""
    return {
        "symbol":   "BTCUSD.P",
        "exchange": "Delta Exchange India",
        "timeframe": "1h",
        "_instruction": snap_meta["tv_instruction"],
        "_status": "REFERENCE_REQUIRED",
        "settings": {
            "swing_length":    50,
            "internal_length": 5,
            "ob_filter":       "ATR",
            "ob_mitigation":   "High/Low",
        },
        "snapshot_timestamp": snap_meta["timestamp"],
        "category": snap_meta["category"],
        "description": snap_meta["description"],
        "python_active_ob_count": active_count,
        "order_blocks": [
            {
                "_comment": "Fill in one entry per LuxAlgo OB box visible at this timestamp",
                "structure_type":     "<internal or swing>",
                "direction":          "<bullish or bearish>",
                "creation_timestamp": "<UTC ISO8601 of OB formation candle if known, else empty>",
                "source_timestamp":   "<UTC ISO8601 of source/extreme candle if known, else empty>",
                "upper":              0.0,
                "lower":              0.0,
                "state":              "<fresh or touched>",
            }
        ],
    }


def main():
    # Load metadata
    with open(DATA_META, encoding="utf-8") as f:
        meta = json.load(f)

    print("=" * 60)
    print("Phase 3D: Generating OB Snapshots")
    print("=" * 60)
    print(f"Data     : {DATA_CSV.name}")
    print(f"Exchange : {meta['exchange']}")
    print(f"Candles  : {meta['candle_count']}")
    print(f"Period   : {meta['first_timestamp']} to {meta['last_timestamp']}")
    print(f"SHA-256  : {meta['sha256']}")
    print()

    # Build engine once (load candles once)
    eng = OBSnapshotEngine.from_csv(DATA_CSV, symbol="BTCUSD.P")
    print(f"Engine loaded: {len(eng.candles)} candles\n")

    all_snapshot_meta = {
        "symbol":           "BTCUSD.P",
        "exchange":         "Delta Exchange India",
        "timeframe":        "1h",
        "data_sha256":      meta["sha256"],
        "candle_count":     meta["candle_count"],
        "first_candle":     meta["first_timestamp"],
        "last_candle":      meta["last_timestamp"],
        "smc_config": {
            "atr_period":       200,
            "atr_mult":         2.0,
            "internal_length":  5,
            "swing_length":     50,
            "ob_filter":        "ATR",
            "ob_mitigation":    "High/Low",
        },
        "snapshots": [],
    }

    for snap_meta in SNAPSHOTS:
        sid  = snap_meta["id"]
        ts   = snap_meta["timestamp"]
        cat  = snap_meta["category"]
        desc = snap_meta["description"]

        print(f"  [{sid}] {ts}  ({cat})")

        snap = eng.snapshot_at(ts)
        inv  = eng.verify_future_data_invariance(ts)

        print(f"    Candles processed : {snap.candles_processed}")
        print(f"    All OBs formed    : {snap.all_count}")
        print(f"    Active OBs        : {snap.active_count}")
        print(f"    Invalidated OBs   : {len(snap.invalidated_obs)}")
        print(f"    Future-invariant  : {inv}")

        # Save Python active OBs
        py_out = OUT_ROOT / f"{sid}_python_active_obs.json"
        py_payload = {
            "snapshot_id":       sid,
            "snapshot_timestamp": ts,
            "category":          cat,
            "description":       desc,
            "candles_processed": snap.candles_processed,
            "all_ob_count":      snap.all_count,
            "active_ob_count":   snap.active_count,
            "invalidated_ob_count": len(snap.invalidated_obs),
            "future_invariant":  inv,
            "active_obs": [ob_record_to_py_entry(r) for r in snap.active_obs],
            "invalidated_obs": [ob_record_to_py_entry(r) for r in snap.invalidated_obs],
        }
        with open(py_out, "w", encoding="utf-8") as f:
            json.dump(py_payload, f, indent=2)
        print(f"    Python OBs saved -> {py_out.name}")

        # Save empty TradingView reference template
        tv_out = OUT_ROOT / f"{sid}_tradingview_reference.json"
        tv_template = build_tv_reference_template(snap_meta, snap.active_count)
        with open(tv_out, "w", encoding="utf-8") as f:
            json.dump(tv_template, f, indent=2)
        print(f"    TV template saved -> {tv_out.name}")

        # Summary for meta file
        all_snapshot_meta["snapshots"].append({
            "id":              sid,
            "timestamp":       ts,
            "category":        cat,
            "description":     desc,
            "active_obs":      snap.active_count,
            "all_obs":         snap.all_count,
            "invalidated_obs": len(snap.invalidated_obs),
            "future_invariant": inv,
            "python_file":     py_out.name,
            "tv_template":     tv_out.name,
            "tv_status":       "REFERENCE_REQUIRED",
        })
        print()

    # Save overall manifest
    manifest_path = OUT_ROOT / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(all_snapshot_meta, f, indent=2)
    print(f"Manifest saved -> {manifest_path}")

    # Print TradingView capture instructions
    print()
    print("=" * 60)
    print("TradingView Capture Instructions")
    print("=" * 60)
    print()
    print("For each snapshot, navigate TradingView to the exact timestamp")
    print("and record ALL visible LuxAlgo OB boxes.")
    print()
    for snap_meta in SNAPSHOTS:
        print(f"[{snap_meta['id']}] {snap_meta['timestamp']}")
        print(f"    {snap_meta['description']}")
        print(f"    Instructions: {snap_meta['tv_instruction'][:100]}...")
        print(f"    Fill in: validation/tradingview_ob_reference/{snap_meta['id']}_tradingview_reference.json")
        print()


if __name__ == "__main__":
    main()
