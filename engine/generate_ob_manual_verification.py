"""
generate_ob_manual_verification.py

Generates a complete, deterministic Order Block inventory and manual
verification pack from the canonical Delta Exchange India BTCUSD 1H dataset.

Outputs:
    validation/ob_manual_verification/all_ob_events.csv
    validation/ob_manual_verification/active_ob_snapshot.csv
    validation/ob_manual_verification/recent_ob_events.csv
    validation/ob_manual_verification/latest_ob_summary.json
    validation/ob_manual_verification/verification_checklist.md
    docs/OB_MANUAL_VERIFICATION.md

Usage (from repo root):
    python engine/generate_ob_manual_verification.py

Requirements:
    - Canonical dataset: data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv
    - engine/ob_snapshot_engine.py
    - Frozen SMC files (NOT modified by this script)
"""

import sys
import csv
import json
import hashlib
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from collections import defaultdict

# ── Path setup ─────────────────────────────────────────────────────────────────
ENGINE    = Path(__file__).parent
REPO_ROOT = ENGINE.parent

sys.path.insert(0, str(ENGINE / "src"))
sys.path.insert(0, str(ENGINE))

from ob_snapshot_engine import OBSnapshotEngine, OBRecord

# ── Canonical paths ─────────────────────────────────────────────────────────────
DATA_CSV  = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026.csv"
DATA_META = REPO_ROOT / "data" / "canonical" / "delta_exchange_india" / "BTCUSD" / "1h" / "2026_metadata.json"
OUT_DIR   = REPO_ROOT / "validation" / "ob_manual_verification"
DOC_PATH  = REPO_ROOT / "docs" / "OB_MANUAL_VERIFICATION.md"

# ── Config ──────────────────────────────────────────────────────────────────────
SMC_CONFIG = {
    "atr_period":      200,
    "atr_mult":        2.0,
    "internal_length": 5,
    "swing_length":    50,
    "ob_filter":       "ATR",
    "ob_mitigation":   "High/Low",
}

# Dataset cutoff = last candle timestamp
DATASET_CUTOFF = "2026-08-20T00:00:00+00:00"


# ── CSV field order ─────────────────────────────────────────────────────────────
CSV_FIELDS = [
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
    "first_touch_timestamp",
    "invalidation_timestamp",
    "pivot_index",
    "pivot_timestamp",
    "pivot_price",
    "is_active",
    "month",
]

CHECKLIST_FIELDS = [
    "ob_id",
    "structure_type",
    "direction",
    "upper_price",
    "lower_price",
    "ob_height",
    "creation_timestamp",
    "break_type",
    "state",
    "categories",
]


def _fmt(v):
    """Format value for CSV/JSON output."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if v is None:
        return ""
    return v


def ob_to_row(ob_id: int, ob: OBRecord) -> dict:
    height = float(ob.upper_price - ob.lower_price)
    month  = ob.creation_timestamp.strftime("%Y-%m")
    return {
        "ob_id":                 ob_id,
        "structure_type":        ob.structure_type,
        "direction":             ob.direction,
        "creation_timestamp":    ob.creation_timestamp.isoformat(),
        "creation_candle_index": ob.creation_candle_index,
        "break_timestamp":       ob.break_timestamp.isoformat() if ob.break_timestamp else "",
        "break_candle_index":    ob.break_candle_index,
        "break_type":            ob.break_type,
        "upper_price":           float(ob.upper_price),
        "lower_price":           float(ob.lower_price),
        "ob_height":             round(height, 2),
        "state":                 ob.state,
        "first_touch_timestamp": ob.first_touch_timestamp.isoformat() if ob.first_touch_timestamp else "",
        "invalidation_timestamp": ob.invalidation_timestamp.isoformat() if ob.invalidation_timestamp else "",
        "pivot_index":           ob.pivot_index if ob.pivot_index is not None else "",
        "pivot_timestamp":       ob.pivot_timestamp.isoformat() if ob.pivot_timestamp else "",
        "pivot_price":           float(ob.pivot_price) if ob.pivot_price is not None else "",
        "is_active":             ob.is_active,
        "month":                 month,
    }


def write_csv(path: Path, rows: list, fields: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_stats(rows: list) -> dict:
    heights     = [r["ob_height"] for r in rows if r["ob_height"]]
    active      = [r for r in rows if r["is_active"]]
    invalidated = [r for r in rows if not r["is_active"]]
    internal    = [r for r in rows if r["structure_type"] == "internal"]
    swing       = [r for r in rows if r["structure_type"] == "swing"]
    bullish     = [r for r in rows if r["direction"] == "bullish"]
    bearish     = [r for r in rows if r["direction"] == "bearish"]
    fresh       = [r for r in rows if r["state"] == "fresh"]
    touched     = [r for r in rows if r["state"] == "touched"]
    inv_state   = [r for r in rows if r["state"] == "invalidated"]

    stats = {
        "total_obs":             len(rows),
        "internal_count":        len(internal),
        "swing_count":           len(swing),
        "bullish_count":         len(bullish),
        "bearish_count":         len(bearish),
        "fresh_count":           len(fresh),
        "touched_count":         len(touched),
        "invalidated_by_state_count": len(inv_state),
        "active_count":          len(active),
        "invalidated_count":     len(invalidated),
        "avg_ob_height":         round(statistics.mean(heights), 2) if heights else None,
        "median_ob_height":      round(statistics.median(heights), 2) if heights else None,
        "earliest_ob_ts":        min(r["creation_timestamp"] for r in rows) if rows else None,
        "latest_ob_ts":          max(r["creation_timestamp"] for r in rows) if rows else None,
    }
    return stats


def monthly_summary(rows: list) -> list:
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["month"]].append(r)
    result = []
    for month in sorted(by_month.keys()):
        grp = by_month[month]
        result.append({
            "month":    month,
            "internal": sum(1 for r in grp if r["structure_type"] == "internal"),
            "swing":    sum(1 for r in grp if r["structure_type"] == "swing"),
            "bullish":  sum(1 for r in grp if r["direction"] == "bullish"),
            "bearish":  sum(1 for r in grp if r["direction"] == "bearish"),
            "fresh":    sum(1 for r in grp if r["state"] == "fresh"),
            "touched":  sum(1 for r in grp if r["state"] == "touched"),
            "invalid":  sum(1 for r in grp if r["state"] == "invalidated"),
            "total":    len(grp),
        })
    return result


def bos_choch_summary(rows: list) -> dict:
    bos   = [r for r in rows if r["break_type"] == "bos"]
    choch = [r for r in rows if r["break_type"] == "choch"]
    return {
        "bos_count":   len(bos),
        "choch_count": len(choch),
        "bos_active":  sum(1 for r in bos   if r["is_active"]),
        "choch_active": sum(1 for r in choch if r["is_active"]),
    }


def select_verification_targets(rows: list) -> list:
    """
    Select OBs for the manual TV checklist.
    Returns list of dicts with 'categories' field added.
    Strategy: gather latest 10 internal, latest 10 swing, latest 10 bullish,
    latest 10 bearish — deduplicate by ob_id, merge category tags.
    """
    active = sorted(
        [r for r in rows if r["is_active"]],
        key=lambda r: r["creation_timestamp"],
        reverse=True,
    )

    def latest_n(lst, n):
        return lst[:n]

    internal = latest_n([r for r in active if r["structure_type"] == "internal"], 10)
    swing    = latest_n([r for r in active if r["structure_type"] == "swing"], 10)
    bullish  = latest_n([r for r in active if r["direction"] == "bullish"], 10)
    bearish  = latest_n([r for r in active if r["direction"] == "bearish"], 10)

    category_map = defaultdict(set)
    for r in internal: category_map[r["ob_id"]].add("latest_internal")
    for r in swing:    category_map[r["ob_id"]].add("latest_swing")
    for r in bullish:  category_map[r["ob_id"]].add("latest_bullish")
    for r in bearish:  category_map[r["ob_id"]].add("latest_bearish")

    seen = set()
    targets = []
    for pool in [internal, swing, bullish, bearish]:
        for r in pool:
            if r["ob_id"] not in seen:
                seen.add(r["ob_id"])
                entry = dict(r)
                entry["categories"] = ", ".join(sorted(category_map[r["ob_id"]]))
                targets.append(entry)

    targets.sort(key=lambda r: r["creation_timestamp"], reverse=True)
    return targets


def write_checklist_md(path: Path, targets: list, gen_ts: str, data_cutoff: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Manual TradingView Order Block Verification Checklist",
        "",
        f"> **Generated**: {gen_ts}",
        f"> **Data cutoff**: {data_cutoff} (last canonical candle)",
        f"> **Exchange**: Delta Exchange India | **Symbol**: BTCUSD.P | **TF**: 1H",
        "",
        "## Instructions",
        "",
        "1. Open TradingView Free → Delta Exchange India → BTCUSD.P → 1H chart",
        "2. Load LuxAlgo Smart Money Concepts indicator with settings:",
        "   - Swing Length: 50 | Internal Length: 5 | OB Filter: ATR | Mitigation: High/Low",
        "3. Scroll chart to approximately the Creation Timestamp for each entry",
        "4. Record what you see in the TradingView fields below",
        "",
        "> ⚠️ **TradingView Free Limitation**: LuxAlgo only shows OBs that are *currently active*",
        "> (not mitigated) in the visible window. Mitigated OBs may not be displayed.",
        "> 'Not visible' does NOT automatically mean the Python engine is wrong.",
        "",
        "---",
        "",
        "## Verification Status Legend",
        "",
        "| Code | Meaning |",
        "|------|---------|",
        "| MATCH | Box found, direction + zone match |",
        "| APPROXIMATE_MATCH | Direction match, price within ±1% |",
        "| NOT_VISIBLE / CANNOT_VERIFY | Box not shown on TradingView Free (expected for old/mitigated OBs) |",
        "| PRICE_MISMATCH | Box found but price differs beyond tolerance |",
        "| DIRECTION_MISMATCH | Box found but direction differs |",
        "| EXTRA_PYTHON_OB | Python shows OB, TradingView does not (may be sub-swing level or filtered) |",
        "| OTHER | Any other case — document in Notes |",
        "",
        "---",
        "",
        f"## Verification Targets ({len(targets)} OBs)",
        "",
    ]

    for i, ob in enumerate(targets, 1):
        lines += [
            f"### OB #{ob['ob_id']} — {ob['structure_type'].upper()} {ob['direction'].upper()}",
            f"**Categories**: {ob['categories']}",
            "",
            "**Python Engine Data** (authoritative):",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| OB ID | {ob['ob_id']} |",
            f"| Structure | {ob['structure_type']} |",
            f"| Direction | {ob['direction']} |",
            f"| Upper Price | {ob['upper_price']:,.1f} |",
            f"| Lower Price | {ob['lower_price']:,.1f} |",
            f"| OB Height | {ob['ob_height']:,.1f} |",
            f"| Created UTC | {ob['creation_timestamp']} |",
            f"| Break Type | {ob['break_type']} |",
            f"| State | {ob['state']} |",
            f"| Is Active | {ob['is_active']} |",
            "",
            "**TradingView Manual Entry** (fill in):",
            "",
            "| Field | Your Observation |",
            "|-------|-----------------|",
            "| Found visually? | `[ ] YES  [ ] NO` |",
            "| Approx Upper | _(fill in)_ |",
            "| Approx Lower | _(fill in)_ |",
            "| Direction matches? | `[ ] YES  [ ] NO` |",
            "| Zone location matches? | `[ ] YES  [ ] NO` |",
            "| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |",
            "| Notes | _(fill in)_ |",
            "",
            "---",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [OK] {path.name} — {len(targets)} verification targets")


def format_table(headers: list, rows: list, key_order: list) -> str:
    """Format a markdown table."""
    col_widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        str_row = []
        for k in key_order:
            v = row.get(k, "")
            if isinstance(v, float):
                s = f"{v:,.1f}"
            elif isinstance(v, bool):
                s = "✓" if v else "✗"
            else:
                s = str(v)
            str_row.append(s)
        for i, s in enumerate(str_row):
            col_widths[i] = max(col_widths[i], len(s))
        str_rows.append(str_row)

    def row_line(cells):
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, col_widths)) + " |"

    lines = [
        row_line(headers),
        "| " + " | ".join("-" * w for w in col_widths) + " |",
    ]
    for r in str_rows:
        lines.append(row_line(r))
    return "\n".join(lines)


def write_main_doc(
    path: Path,
    all_rows: list,
    active_rows: list,
    recent_rows: list,
    targets: list,
    stats: dict,
    monthly: list,
    bos_choch: dict,
    meta: dict,
    gen_ts: str,
    data_cutoff: str,
    all_csv_sha256: str,
):
    path.parent.mkdir(parents=True, exist_ok=True)

    # Build sections
    doc = []

    # ── Header ───────────────────────────────────────────────────────────────────
    doc += [
        "# QuantEdge AI V2 — Order Block Manual Verification Pack",
        "",
        "## 1. Dataset & Engine Provenance",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Exchange | Delta Exchange India |",
        f"| API Symbol | BTCUSD |",
        f"| TradingView Symbol | BTCUSD.P |",
        f"| Timeframe | 1H |",
        f"| Dataset Source | `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv` |",
        f"| Dataset SHA-256 | `{meta['sha256']}` |",
        f"| Dataset Period | {meta['first_timestamp']} → {data_cutoff} |",
        f"| Candle Count | {meta['candle_count']:,} |",
        f"| ATR Period | {SMC_CONFIG['atr_period']} |",
        f"| ATR Multiplier | {SMC_CONFIG['atr_mult']} |",
        f"| Internal Length | {SMC_CONFIG['internal_length']} |",
        f"| Swing Length | {SMC_CONFIG['swing_length']} |",
        f"| OB Filter | {SMC_CONFIG['ob_filter']} |",
        f"| OB Mitigation Rule | {SMC_CONFIG['ob_mitigation']} |",
        f"| Generation Timestamp | {gen_ts} |",
        f"| all_ob_events.csv SHA-256 | `{all_csv_sha256}` |",
        "",
        "> **Data Cutoff**: The canonical dataset ends at **{cutoff}**.",
        "> Python OBs are only computed through this timestamp.",
        "> Do NOT assume engine data extends to the current wall-clock time.",
        "",
    ]
    doc[-3] = doc[-3].format(cutoff=data_cutoff)

    # ── TradingView Limitation ────────────────────────────────────────────────────
    doc += [
        "## 2. TradingView Free Limitation",
        "",
        "> ⚠️ **LuxAlgo on TradingView Free** does not preserve historical mitigated OBs.",
        "> When you scroll back to a historical candle, LuxAlgo only shows OBs that are",
        "> **currently unmitigated** (active) relative to the most recent chart bar.",
        ">",
        "> Therefore:",
        "> - 'Not visible on TradingView Free' **MUST NOT** be classified as 'Python incorrect.'",
        "> - Only **currently-active OBs** (at the dataset cutoff) can be reliably verified",
        ">   against TradingView Free.",
        "> - TradingView Bar Replay (Premium only) would be required for exact historical validation.",
        "",
        "### Manual Validation Classification",
        "",
        "| Status | Meaning |",
        "|--------|---------|",
        "| MATCH | Price zone found, direction + zone match within tolerance |",
        "| APPROXIMATE_MATCH | Direction match, price within ±1% |",
        "| NOT_VISIBLE / CANNOT_VERIFY | OB not shown on TradingView Free (expected for mitigated) |",
        "| PRICE_MISMATCH | OB found but price differs beyond tolerance |",
        "| DIRECTION_MISMATCH | OB found but direction differs |",
        "| EXTRA_PYTHON_OB | Python shows active OB, TradingView does not show it |",
        "| OTHER | Document in notes |",
        "",
    ]

    # ── Statistics ────────────────────────────────────────────────────────────────
    doc += [
        "## 3. All-Time OB Statistics",
        "",
        f"All statistics are computed from the full 2026 canonical dataset ({meta['candle_count']:,} candles).",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total OBs | {stats['total_obs']:,} |",
        f"| Internal OBs | {stats['internal_count']:,} |",
        f"| Swing OBs | {stats['swing_count']:,} |",
        f"| Bullish OBs | {stats['bullish_count']:,} |",
        f"| Bearish OBs | {stats['bearish_count']:,} |",
        f"| Fresh (at cutoff) | {stats['fresh_count']:,} |",
        f"| Touched (at cutoff) | {stats['touched_count']:,} |",
        f"| Invalidated (by state) | {stats['invalidated_by_state_count']:,} |",
        f"| Active (at cutoff) | {stats['active_count']:,} |",
        f"| Invalidated (by lifecycle) | {stats['invalidated_count']:,} |",
        f"| BOS-triggered OBs | {bos_choch['bos_count']:,} |",
        f"| CHOCH-triggered OBs | {bos_choch['choch_count']:,} |",
        f"| Average OB Height | {stats['avg_ob_height']:,.1f} |",
        f"| Median OB Height | {stats['median_ob_height']:,.1f} |",
        f"| Earliest OB Created | {stats['earliest_ob_ts']} |",
        f"| Latest OB Created | {stats['latest_ob_ts']} |",
        "",
    ]

    # ── Monthly summary ───────────────────────────────────────────────────────────
    doc += [
        "## 4. OB Creation by Month",
        "",
        format_table(
            ["Month", "Internal", "Swing", "Bullish", "Bearish", "Fresh", "Touched", "Invalidated", "Total"],
            monthly,
            ["month", "internal", "swing", "bullish", "bearish", "fresh", "touched", "invalid", "total"],
        ),
        "",
    ]

    # ── Active OBs at cutoff ──────────────────────────────────────────────────────
    active_sorted = sorted(active_rows, key=lambda r: r["creation_timestamp"], reverse=True)
    doc += [
        f"## 5. All Active OBs at Dataset Cutoff ({data_cutoff})",
        "",
        f"**{len(active_sorted)} active OBs** at the dataset cutoff.",
        "",
        format_table(
            ["ID", "Structure", "Direction", "Upper", "Lower", "Height", "Created UTC", "Break", "State"],
            active_sorted,
            ["ob_id", "structure_type", "direction", "upper_price", "lower_price", "ob_height",
             "creation_timestamp", "break_type", "state"],
        ),
        "",
    ]

    # ── Recent OBs ───────────────────────────────────────────────────────────────
    doc += [
        "## 6. Most Recent 50 OB Creation Events",
        "",
        "Sorted newest first. Use these timestamps to navigate TradingView.",
        "",
        format_table(
            ["ID", "Structure", "Direction", "Upper", "Lower", "Height", "Created UTC", "Break", "State", "Active"],
            recent_rows,
            ["ob_id", "structure_type", "direction", "upper_price", "lower_price", "ob_height",
             "creation_timestamp", "break_type", "state", "is_active"],
        ),
        "",
    ]

    # ── Top Verification Targets ──────────────────────────────────────────────────
    doc += [
        "## 7. Top Manual Verification Targets",
        "",
        "These are the most useful recent active OBs for manual TradingView comparison.",
        "Each OB appears once with its category tags.",
        "",
        f"**{len(targets)} unique targets** selected from:",
        "- Latest 10 active internal OBs",
        "- Latest 10 active swing OBs",
        "- Latest 10 active bullish OBs",
        "- Latest 10 active bearish OBs",
        "",
        format_table(
            ["ID", "Structure", "Direction", "Upper", "Lower", "Height", "Created UTC", "Break", "State", "Categories"],
            targets,
            ["ob_id", "structure_type", "direction", "upper_price", "lower_price", "ob_height",
             "creation_timestamp", "break_type", "state", "categories"],
        ),
        "",
        "> See `validation/ob_manual_verification/verification_checklist.md` for the fillable checklist.",
        "",
    ]

    # ── Files generated ───────────────────────────────────────────────────────────
    doc += [
        "## 8. Generated Files",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `validation/ob_manual_verification/all_ob_events.csv` | Every OB created (all-time, all states) |",
        "| `validation/ob_manual_verification/active_ob_snapshot.csv` | Active OBs at dataset cutoff only |",
        "| `validation/ob_manual_verification/recent_ob_events.csv` | Most recent 50 OB creation events |",
        "| `validation/ob_manual_verification/latest_ob_summary.json` | Machine-readable summary + stats |",
        "| `validation/ob_manual_verification/verification_checklist.md` | Fillable manual TV checklist |",
        "| `docs/OB_MANUAL_VERIFICATION.md` | This document |",
        "",
    ]

    # ── Verification Status ───────────────────────────────────────────────────────
    doc += [
        "## 9. Duplication Notes",
        "",
        "Some price zones appear in **both internal and swing** OB lists.",
        "This is by design — internal and swing OBs are formed by different structure break types",
        "and carry different trading significance.",
        "The engine correctly tracks them as separate events.",
        "They are NOT silently merged in any output file.",
        "",
        "If two rows share identical `upper_price`/`lower_price` but differ in `structure_type`,",
        "they represent the same price zone detected at two different structural levels.",
        "",
    ]

    # ── Phase 3D Status ───────────────────────────────────────────────────────────
    doc += [
        "---",
        "",
        "## PHASE 3D MANUAL OB VERIFICATION STATUS",
        "",
        "| Item | Status |",
        "|------|--------|",
        "| Python OB inventory | ✅ COMPLETE |",
        "| Delta Exchange India BTCUSD canonical dataset | ✅ VERIFIED |",
        "| Binance or proxy data | ✅ NOT USED |",
        "| All-time OB history | ✅ GENERATED |",
        "| Latest active OB report | ✅ GENERATED |",
        "| TradingView exact historical validation | ❌ NOT CLAIMED |",
        "| TradingView Free limitation | ✅ DOCUMENTED |",
        "| Phase 4 strategy development | 🔒 NOT STARTED |",
        "",
        "> **Phase 4 readiness**: PENDING MANUAL REVIEW",
        "> Complete the verification checklist (`verification_checklist.md`) before starting Phase 4.",
        "",
        "---",
        "",
        "*Generated by `engine/generate_ob_manual_verification.py`*  ",
        f"*Generation timestamp: {gen_ts}*  ",
        f"*Dataset SHA-256: {meta['sha256']}*  ",
    ]

    path.write_text("\n".join(doc), encoding="utf-8")
    print(f"  [OK] {path.name}")


def main():
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("=" * 60)
    print("Phase 3D: Generating OB Manual Verification Pack")
    print("=" * 60)
    print(f"Dataset  : {DATA_CSV}")
    print(f"Output   : {OUT_DIR}")
    print(f"Cutoff   : {DATASET_CUTOFF}")

    # ── Load metadata ─────────────────────────────────────────────────────────────
    meta = json.loads(DATA_META.read_text(encoding="utf-8"))
    print(f"Candles  : {meta['candle_count']:,}")
    print(f"SHA-256  : {meta['sha256']}")

    # ── Run engine at dataset cutoff ──────────────────────────────────────────────
    print("\nLoading OBSnapshotEngine...")
    eng = OBSnapshotEngine.from_csv(str(DATA_CSV))
    print(f"Engine loaded: {len(eng.candles):,} candles")

    print(f"Running snapshot at cutoff: {DATASET_CUTOFF}")
    snap = eng.snapshot_at(DATASET_CUTOFF)
    print(f"  All OBs formed    : {snap.all_count:,}")
    print(f"  Active OBs        : {snap.active_count:,}")
    print(f"  Invalidated OBs   : {len(snap.invalidated_obs):,}")

    # ── Build rows ────────────────────────────────────────────────────────────────
    print("\nBuilding OB rows...")
    all_obs_sorted = sorted(snap.all_obs, key=lambda r: r.creation_timestamp)
    all_rows   = [ob_to_row(i + 1, ob) for i, ob in enumerate(all_obs_sorted)]
    active_rows = [r for r in all_rows if r["is_active"]]
    recent_rows = sorted(all_rows, key=lambda r: r["creation_timestamp"], reverse=True)[:50]

    # ── Statistics ────────────────────────────────────────────────────────────────
    stats   = compute_stats(all_rows)
    monthly = monthly_summary(all_rows)
    bos_choch = bos_choch_summary(all_rows)

    # ── Verification targets ──────────────────────────────────────────────────────
    targets = select_verification_targets(all_rows)

    # ── Write CSV files ───────────────────────────────────────────────────────────
    print("\nWriting output files...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_csv_path = OUT_DIR / "all_ob_events.csv"
    write_csv(all_csv_path, all_rows, CSV_FIELDS)
    all_csv_sha256 = sha256_of_file(all_csv_path)
    print(f"  [OK] all_ob_events.csv — {len(all_rows):,} rows | SHA-256: {all_csv_sha256}")

    active_csv_path = OUT_DIR / "active_ob_snapshot.csv"
    write_csv(active_csv_path, active_rows, CSV_FIELDS)
    print(f"  [OK] active_ob_snapshot.csv — {len(active_rows):,} rows")

    recent_csv_path = OUT_DIR / "recent_ob_events.csv"
    write_csv(recent_csv_path, recent_rows, CSV_FIELDS)
    print(f"  [OK] recent_ob_events.csv — {len(recent_rows):,} rows")

    # ── Write JSON summary ────────────────────────────────────────────────────────
    summary = {
        "generated_at":         gen_ts,
        "dataset_cutoff":       DATASET_CUTOFF,
        "dataset_sha256":       meta["sha256"],
        "candle_count":         meta["candle_count"],
        "dataset_path":         "data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv",
        "smc_config":           SMC_CONFIG,
        "all_ob_events_sha256": all_csv_sha256,
        "statistics":           stats,
        "monthly_summary":      monthly,
        "bos_choch_summary":    bos_choch,
        "active_ob_count":      len(active_rows),
        "verification_target_count": len(targets),
        "top_10_active_obs":    recent_rows[:10],
        "top_10_internal_active": [
            r for r in sorted(active_rows, key=lambda r: r["creation_timestamp"], reverse=True)
            if r["structure_type"] == "internal"
        ][:10],
        "top_10_swing_active": [
            r for r in sorted(active_rows, key=lambda r: r["creation_timestamp"], reverse=True)
            if r["structure_type"] == "swing"
        ][:10],
    }

    json_path = OUT_DIR / "latest_ob_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"  [OK] latest_ob_summary.json")

    # ── Write verification checklist ──────────────────────────────────────────────
    checklist_path = OUT_DIR / "verification_checklist.md"
    write_checklist_md(checklist_path, targets, gen_ts, DATASET_CUTOFF)

    # ── Write main doc ────────────────────────────────────────────────────────────
    write_main_doc(
        DOC_PATH, all_rows, active_rows, recent_rows, targets,
        stats, monthly, bos_choch, meta, gen_ts, DATASET_CUTOFF, all_csv_sha256,
    )

    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"  Total OBs           : {stats['total_obs']:,}")
    print(f"  Internal OBs        : {stats['internal_count']:,}")
    print(f"  Swing OBs           : {stats['swing_count']:,}")
    print(f"  Active at cutoff    : {stats['active_count']:,}")
    print(f"  Invalidated         : {stats['invalidated_count']:,}")
    print(f"  Latest OB created   : {stats['latest_ob_ts']}")
    print(f"  Verification targets: {len(targets)}")
    print()
    print("Output files:")
    for p in [all_csv_path, active_csv_path, recent_csv_path, json_path, checklist_path, DOC_PATH]:
        size = p.stat().st_size
        print(f"  {p.relative_to(REPO_ROOT)}  ({size:,} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
