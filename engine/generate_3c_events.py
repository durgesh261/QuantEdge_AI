"""
Phase 3C: Generate SMC events from Delta Exchange BTCUSDT 1H data.

Source: Delta Exchange (global) BTCUSDT perpetual futures
Data file: engine/data/historical/BTCUSDT.P/1h/2026_delta.csv

This uses the SAME frozen SMC engine as production.
DO NOT MODIFY: structure.py, order_blocks.py, volatility.py

Run from repo root:
    python engine/generate_3c_events.py
"""

import sys
import csv
import json
import hashlib
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone

ENGINE = Path(__file__).parent
sys.path.insert(0, str(ENGINE / "src"))

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import detect_order_blocks_streaming, OrderBlockConfig
from quantedge.smc.models import PivotPoint

REPO      = ENGINE.parent
DATA_ROOT = ENGINE / "data" / "historical"
OUT_ROOT  = REPO / "validation" / "phase3c"

SYMBOL_LOCAL  = "BTCUSDT.P"
DATA_FILE     = DATA_ROOT / SYMBOL_LOCAL / "1h" / "2026_delta.csv"
META_FILE     = DATA_ROOT / SYMBOL_LOCAL / "1h" / "2026_delta_metadata.json"

# SMC config — mirrors production (frozen)
ATR_PERIOD = 200
ATR_MULT   = 2.0
INT_LEN    = 5
SW_LEN     = 50

# Validation windows (same structure as Phase 3B for comparability)
WINDOWS = [
    {
        "id":          "W3C_1",
        "category":    "bearish_trend",
        "description": "Jan-Feb 2026 BTC correction",
        "start":       datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc),
        "end":         datetime(2026, 2, 10, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id":          "W3C_2",
        "category":    "bullish_trend",
        "description": "Mar-Apr 2026 recovery rally",
        "start":       datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
        "end":         datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id":          "W3C_3",
        "category":    "ranging",
        "description": "Apr-May 2026 consolidation",
        "start":       datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc),
        "end":         datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id":          "W3C_4",
        "category":    "bullish_to_bearish",
        "description": "Jun 2026 transition",
        "start":       datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        "end":         datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id":          "W3C_5",
        "category":    "recent",
        "description": "Jul-Aug 2026 manual comparison window",
        "start":       datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
        "end":         datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    },
]


def load_csv(path: Path) -> list:
    """Load OHLCV CSV into Candle list."""
    candles = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            c = Candle(
                timestamp   = ts,
                open        = Decimal(row["open"]),
                high        = Decimal(row["high"]),
                low         = Decimal(row["low"]),
                close       = Decimal(row["close"]),
                volume      = Decimal(row["volume"]),
                timeframe   = Timeframe.H1,
                symbol      = SYMBOL_LOCAL,
                source      = MarketDataSource.HISTORICAL,
            )
            candles.append(c)
    return candles


def run_smc_full(candles: list) -> dict:
    """Run full SMC pipeline on the entire dataset and return all events."""
    parsed = parse_candles_with_volatility(candles, ATR_PERIOD, ATR_MULT)

    int_cfg = StructureConfig(length=INT_LEN, structure_type=StructureType.INTERNAL)
    sw_cfg  = StructureConfig(length=SW_LEN,  structure_type=StructureType.SWING)

    int_det = StructureDetector(int_cfg)
    sw_det  = StructureDetector(sw_cfg)

    int_breaks = []
    sw_breaks  = []
    int_pivots = []
    sw_pivots  = []

    prev_iph = prev_ipl = prev_sph = prev_spl = None

    for i, pc in enumerate(parsed):
        int_det.process_candle(pc, i)
        sw_det.process_candle(pc, i)

        iph = int_det.state.pivot_high
        ipl = int_det.state.pivot_low
        sph = sw_det.state.pivot_high
        spl = sw_det.state.pivot_low

        if iph and iph.index != prev_iph:
            int_pivots.append(PivotPoint(iph.index, iph.timestamp, iph.price, True, iph.candle))
            prev_iph = iph.index
        if ipl and ipl.index != prev_ipl:
            int_pivots.append(PivotPoint(ipl.index, ipl.timestamp, ipl.price, False, ipl.candle))
            prev_ipl = ipl.index
        if sph and sph.index != prev_sph:
            sw_pivots.append(PivotPoint(sph.index, sph.timestamp, sph.price, True, sph.candle))
            prev_sph = sph.index
        if spl and spl.index != prev_spl:
            sw_pivots.append(PivotPoint(spl.index, spl.timestamp, spl.price, False, spl.candle))
            prev_spl = spl.index

    # Second pass — clean detectors for break collection
    int_det2 = StructureDetector(int_cfg)
    sw_det2  = StructureDetector(sw_cfg)
    for i, pc in enumerate(parsed):
        int_breaks.extend(int_det2.process_candle(pc, i))
        sw_breaks.extend(sw_det2.process_candle(pc, i))

    cfg = OrderBlockConfig(
        internal_length=INT_LEN, swing_length=SW_LEN,
        atr_period=ATR_PERIOD, atr_multiplier=ATR_MULT,
    )
    int_obs = detect_order_blocks_streaming(
        parsed_candles=parsed,
        internal_breaks=int_breaks, swing_breaks=[],
        internal_pivots=int_pivots, swing_pivots=sw_pivots,
        config=cfg,
    )
    sw_obs = detect_order_blocks_streaming(
        parsed_candles=parsed,
        internal_breaks=[], swing_breaks=sw_breaks,
        internal_pivots=int_pivots, swing_pivots=sw_pivots,
        config=cfg,
    )

    return {
        "int_pivots":  int_pivots,
        "sw_pivots":   sw_pivots,
        "int_breaks":  int_breaks,
        "sw_breaks":   sw_breaks,
        "int_obs":     int_obs,
        "sw_obs":      sw_obs,
    }


def filter_window(items, start_dt: datetime, end_dt: datetime, ts_attr: str = "timestamp") -> list:
    result = []
    for item in items:
        ts = getattr(item, ts_attr, None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if start_dt <= ts < end_dt:
            result.append(item)
    return result


def break_to_dict(b, stream: str) -> dict:
    return {
        "stream":     stream,
        "type":       b.break_type.value if hasattr(b.break_type, "value") else str(b.break_type),
        "direction":  b.direction.value  if hasattr(b.direction, "value")  else str(b.direction),
        "timestamp":  b.timestamp.isoformat(),
        "price":      str(b.price),
    }


def ob_to_dict(ob, stream: str) -> dict:
    return {
        "stream":       stream,
        "type":         "OB_" + (ob.direction.value.upper() if hasattr(ob.direction, "value") else str(ob.direction).upper()),
        "direction":    ob.direction.value if hasattr(ob.direction, "value") else str(ob.direction),
        "timestamp":    ob.timestamp.isoformat(),
        "top":          str(ob.top),
        "bottom":       str(ob.bottom),
    }


def write_window_csv(out_path: Path, window: dict, events: list):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    idx = 0
    for ev in events:
        rows.append({
            "idx":         idx,
            "timestamp":   ev.get("timestamp", ""),
            "event_type":  ev.get("type", ""),
            "stream":      ev.get("stream", ""),
            "direction":   ev.get("direction", ""),
            "price":       ev.get("price", ""),
            "ob_top":      ev.get("top", ""),
            "ob_bottom":   ev.get("bottom", ""),
        })
        idx += 1

    fieldnames = ["idx", "timestamp", "event_type", "stream", "direction", "price", "ob_top", "ob_bottom"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def print_summary(tag: str, all_ev: dict):
    int_bos   = [b for b in all_ev["int_breaks"] if "BOS"   in str(b.break_type)]
    int_choch = [b for b in all_ev["int_breaks"] if "CHOCH" in str(b.break_type)]
    sw_bos    = [b for b in all_ev["sw_breaks"]  if "BOS"   in str(b.break_type)]
    sw_choch  = [b for b in all_ev["sw_breaks"]  if "CHOCH" in str(b.break_type)]
    print(f"\n  {tag}:")
    print(f"    Internal BOS   : {len(int_bos):4d}")
    print(f"    Internal CHOCH : {len(int_choch):4d}")
    print(f"    Swing BOS      : {len(sw_bos):4d}")
    print(f"    Swing CHOCH    : {len(sw_choch):4d}")
    print(f"    Internal OBs   : {len(all_ev['int_obs']):4d}")
    print(f"    Swing OBs      : {len(all_ev['sw_obs']):4d}")


def main():
    print("=" * 60)
    print("Phase 3C: Delta BTCUSDT SMC Event Generation")
    print("=" * 60)

    if not DATA_FILE.exists():
        print(f"[ERROR] Data file not found: {DATA_FILE}")
        print("Run: python engine/download_delta_btcusdt.py first")
        sys.exit(1)

    # Load metadata
    with open(META_FILE, encoding="utf-8") as f:
        meta = json.load(f)

    print(f"\nData source  : {meta.get('exchange')}")
    print(f"Symbol       : {meta.get('delta_symbol')}")
    print(f"Candles      : {meta.get('candle_count')}")
    print(f"Period       : {meta.get('first_timestamp')} to {meta.get('last_timestamp')}")
    print(f"Gaps         : {meta.get('gap_count')} (max {meta.get('max_gap_hours', 0):.1f}H)")
    print(f"Invalid OHLC : {meta.get('invalid_ohlc')}")
    print(f"SHA-256      : {meta.get('sha256')}")

    print("\nLoading candles...")
    all_candles = load_csv(DATA_FILE)
    print(f"  Loaded {len(all_candles)} candles")

    print("\nRunning SMC engine on full dataset...")
    all_ev = run_smc_full(all_candles)
    print_summary("Full 2026 dataset", all_ev)

    # Per-window export
    print("\nGenerating per-window event CSVs...")
    summary = {
        "symbol":       SYMBOL_LOCAL,
        "delta_symbol": "BTCUSDT",
        "exchange":     "Delta Exchange (global)",
        "sha256":       meta.get("sha256"),
        "candle_count": len(all_candles),
        "first_ts":     all_candles[0].timestamp.isoformat() if all_candles else None,
        "last_ts":      all_candles[-1].timestamp.isoformat() if all_candles else None,
        "windows":      [],
    }

    for win in WINDOWS:
        wid   = win["id"]
        wcat  = win["category"]
        wstart = win["start"]
        wend   = win["end"]

        int_breaks_w = filter_window(all_ev["int_breaks"], wstart, wend)
        sw_breaks_w  = filter_window(all_ev["sw_breaks"],  wstart, wend)
        int_obs_w    = filter_window(all_ev["int_obs"],    wstart, wend)
        sw_obs_w     = filter_window(all_ev["sw_obs"],     wstart, wend)
        int_pivots_w = filter_window(all_ev["int_pivots"], wstart, wend)
        sw_pivots_w  = filter_window(all_ev["sw_pivots"],  wstart, wend)

        events = []
        for b in int_breaks_w:
            events.append(break_to_dict(b, "internal"))
        for b in sw_breaks_w:
            events.append(break_to_dict(b, "swing"))
        for ob in int_obs_w:
            events.append(ob_to_dict(ob, "internal"))
        for ob in sw_obs_w:
            events.append(ob_to_dict(ob, "swing"))
        events.sort(key=lambda e: e["timestamp"])

        out_path = OUT_ROOT / SYMBOL_LOCAL / f"{wid}_{wcat}.csv"
        write_window_csv(out_path, win, events)

        int_bos   = [b for b in int_breaks_w if "BOS"   in str(b.break_type)]
        int_choch = [b for b in int_breaks_w if "CHOCH" in str(b.break_type)]
        sw_bos    = [b for b in sw_breaks_w  if "BOS"   in str(b.break_type)]
        sw_choch  = [b for b in sw_breaks_w  if "CHOCH" in str(b.break_type)]

        wsum = {
            "window":      wid,
            "category":    wcat,
            "start":       wstart.isoformat(),
            "end":         wend.isoformat(),
            "int_bos":     len(int_bos),
            "int_choch":   len(int_choch),
            "sw_bos":      len(sw_bos),
            "sw_choch":    len(sw_choch),
            "int_obs":     len(int_obs_w),
            "sw_obs":      len(sw_obs_w),
            "int_pivots":  len(int_pivots_w),
            "sw_pivots":   len(sw_pivots_w),
            "total_events": len(events),
            "csv":         str(out_path.relative_to(REPO)),
        }
        summary["windows"].append(wsum)
        print(f"  {wid} ({wcat}): {len(int_bos)} iBOS + {len(int_choch)} iCHO "
              f"+ {len(sw_bos)} sBOS + {len(sw_choch)} sCHO "
              f"+ {len(int_obs_w)} iOB + {len(sw_obs_w)} sOB -> {out_path.name}")

    # Write summary JSON
    summary_path = OUT_ROOT / SYMBOL_LOCAL / "summary_3c.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Summary -> {summary_path.relative_to(REPO)}")
    print("\nDone.")


if __name__ == "__main__":
    main()
