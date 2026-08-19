"""
OB Diagnostic: Trace the full Order Block pipeline on real BTCUSD.P data.

This script answers:
  A. Is OB creation even invoked?
  B. Is the pivot search range valid?
  C. Are parsed values valid?
  D. Does _find_broken_pivot_index return a valid index?
  E. What is the exact rejection reason if OB = 0?

Run from engine/ directory:
    python ob_diagnostic.py
"""

import sys
import csv
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from quantedge.market_data.models import Candle, Timeframe
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import OrderBlockDetector, OrderBlockConfig
from quantedge.smc.models import TrendDirection, BreakType, PivotPoint


# ─── Load real BTCUSD.P data ──────────────────────────────────────────────────

DATA_FILE = Path(__file__).parent / "data" / "historical" / "BTCUSD.P" / "1h" / "2024.csv"

def load_candles(path: Path) -> list[Candle]:
    candles = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = row["timestamp"]
            # Parse ISO 8601
            if ts_str.endswith("+00:00"):
                ts = datetime.fromisoformat(ts_str)
            else:
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            candles.append(Candle(
                symbol="BTCUSD.P",
                timeframe=Timeframe.H1,
                timestamp=ts,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row.get("volume", "0")),
            ))
    return candles


# ─── Main Diagnostic ──────────────────────────────────────────────────────────

def run_diagnostic():
    print("=" * 70)
    print("OB DIAGNOSTIC — BTCUSD.P 1H 2024")
    print("=" * 70)

    # 1. Load data
    print(f"\n[1] Loading candles from {DATA_FILE}")
    candles = load_candles(DATA_FILE)
    print(f"    Loaded {len(candles)} candles")
    print(f"    First: {candles[0].timestamp}  Last: {candles[-1].timestamp}")

    # 2. Parse with volatility  (ATR 200, multiplier 2.0 — canonical)
    ATR_PERIOD = 200
    ATR_MULT = 2.0
    print(f"\n[2] Parsing with volatility (ATR={ATR_PERIOD}, mult={ATR_MULT})")
    parsed = parse_candles_with_volatility(candles, atr_period=ATR_PERIOD, atr_multiplier=ATR_MULT)
    print(f"    Parsed {len(parsed)} candles")

    # Count high-volatility candles
    hv_count = sum(1 for p in parsed if p.is_high_volatility)
    print(f"    High-volatility candles: {hv_count} / {len(parsed)} ({100*hv_count/len(parsed):.2f}%)")
    
    # Show ATR stats
    atr_vals = [p.atr_value for p in parsed if p.atr_value and p.atr_value > 0]
    if atr_vals:
        print(f"    ATR range: {min(atr_vals):.2f} – {max(atr_vals):.2f}")
        print(f"    ATR mean:  {sum(atr_vals)/len(atr_vals):.2f}")

    # 3. Run structure detection — collect ALL breaks + track pivots
    print(f"\n[3] Running structure detection (internal length=5, swing length=50)")
    
    # INTERNAL
    int_detector = StructureDetector(StructureConfig(5, StructureType.INTERNAL))
    int_breaks = []
    int_pivot_snapshots = {}  # break_idx -> (pivot_high, pivot_low) at time of break

    for i, pc in enumerate(parsed):
        breaks = int_detector.process_candle(pc, i)
        for b in breaks:
            # Capture pivot state at break time
            ph = int_detector.state.pivot_high
            pl = int_detector.state.pivot_low
            int_pivot_snapshots[b.index] = (ph, pl)
            int_breaks.append(b)

    int_highs, int_lows = int_detector.get_confirmed_pivots()
    all_int_pivots = int_highs + int_lows

    # SWING
    sw_detector = StructureDetector(StructureConfig(50, StructureType.SWING))
    sw_breaks = []
    sw_pivot_snapshots = {}

    for i, pc in enumerate(parsed):
        breaks = sw_detector.process_candle(pc, i)
        for b in breaks:
            ph = sw_detector.state.pivot_high
            pl = sw_detector.state.pivot_low
            sw_pivot_snapshots[b.index] = (ph, pl)
            sw_breaks.append(b)

    sw_highs, sw_lows = sw_detector.get_confirmed_pivots()
    all_sw_pivots = sw_highs + sw_lows

    print(f"    Internal: {len(int_breaks)} breaks  ({sum(1 for b in int_breaks if b.break_type==BreakType.BOS)} BOS, {sum(1 for b in int_breaks if b.break_type==BreakType.CHOCH)} CHOCH)")
    print(f"    Swing:    {len(sw_breaks)} breaks  ({sum(1 for b in sw_breaks if b.break_type==BreakType.BOS)} BOS, {sum(1 for b in sw_breaks if b.break_type==BreakType.CHOCH)} CHOCH)")
    print(f"    Internal pivots in get_confirmed_pivots(): {len(all_int_pivots)}")
    print(f"    Swing    pivots in get_confirmed_pivots(): {len(all_sw_pivots)}")

    # 4. Run OB detection — instrument _find_broken_pivot_index
    print(f"\n[4] OB Detection — instrumenting pipeline")

    ob_config = OrderBlockConfig(
        internal_length=5,
        swing_length=50,
        atr_period=ATR_PERIOD,
        atr_multiplier=ATR_MULT,
    )
    ob_detector = OrderBlockDetector(ob_config)

    # ── Counters ──────────────────────────────────────────────────────────────
    counters = {
        "internal_breaks_attempted": 0,
        "swing_breaks_attempted": 0,
        "pivot_not_found_internal": 0,
        "pivot_not_found_swing": 0,
        "range_invalid_internal": 0,
        "range_invalid_swing": 0,
        "ob_created_internal": 0,
        "ob_created_swing": 0,
    }
    rejection_details_internal = []
    rejection_details_swing = []

    def trace_break(brk, structure_type, pivots, all_pivots_list, rejection_list, cnt_attempt, cnt_notfound, cnt_invalid, cnt_created):
        """Trace a single break through the OB pipeline."""
        counters[cnt_attempt] += 1
        break_idx = brk.index
        is_bullish = brk.direction == TrendDirection.BULLISH

        # Replicate _find_broken_pivot_index
        search_start = ob_detector._find_broken_pivot_index(brk, all_int_pivots, all_sw_pivots, structure_type)
        search_end = break_idx

        detail = {
            "break_idx": break_idx,
            "break_ts": str(brk.timestamp),
            "break_type": brk.break_type.value,
            "direction": brk.direction.value,
            "break_price": float(brk.price),
            "search_start": search_start,
            "search_end": search_end,
            "range_len": search_end - search_start,
        }

        if search_start < 0:
            detail["rejection"] = "search_start < 0 (pivot not found)"
            counters[cnt_notfound] += 1
            rejection_list.append(detail)
            return None

        if search_start >= search_end:
            detail["rejection"] = f"range invalid: search_start({search_start}) >= search_end({search_end})"
            counters[cnt_invalid] += 1
            rejection_list.append(detail)
            return None

        # Valid range — attempt OB creation
        ob = ob_detector._create_order_block_from_break(
            parsed_candles=parsed,
            break_event=brk,
            structure_type=structure_type,
            internal_pivots=all_int_pivots,
            swing_pivots=all_sw_pivots,
        )
        if ob:
            counters[cnt_created] += 1
            detail["rejection"] = None
            detail["ob_created"] = f"OB at idx={ob.index}, top={float(ob.top_price):.2f}, bot={float(ob.bottom_price):.2f}"
        else:
            detail["rejection"] = "OB creation returned None (range empty or other)"
        rejection_list.append(detail)
        return ob

    obs_internal = []
    for brk in int_breaks:
        ob = trace_break(brk, "internal", all_int_pivots, all_int_pivots,
                         rejection_details_internal,
                         "internal_breaks_attempted", "pivot_not_found_internal",
                         "range_invalid_internal", "ob_created_internal")
        if ob:
            obs_internal.append(ob)

    obs_swing = []
    for brk in sw_breaks:
        ob = trace_break(brk, "swing", all_sw_pivots, all_sw_pivots,
                         rejection_details_swing,
                         "swing_breaks_attempted", "pivot_not_found_swing",
                         "range_invalid_swing", "ob_created_swing")
        if ob:
            obs_swing.append(ob)

    # ── Report ────────────────────────────────────────────────────────────────
    print("\n[5] COUNTERS")
    print(f"    Internal breaks attempted:     {counters['internal_breaks_attempted']}")
    print(f"    Internal pivot not found:      {counters['pivot_not_found_internal']}")
    print(f"    Internal range invalid:        {counters['range_invalid_internal']}")
    print(f"    Internal OB created:           {counters['ob_created_internal']}")
    print()
    print(f"    Swing breaks attempted:        {counters['swing_breaks_attempted']}")
    print(f"    Swing pivot not found:         {counters['pivot_not_found_swing']}")
    print(f"    Swing range invalid:           {counters['range_invalid_swing']}")
    print(f"    Swing OB created:              {counters['ob_created_swing']}")

    # ── Rejection reason breakdown ─────────────────────────────────────────────
    def summarize_rejections(details, label):
        reasons = {}
        for d in details:
            r = d.get("rejection") or "OB created"
            reasons[r] = reasons.get(r, 0) + 1
        print(f"\n    {label} rejection breakdown:")
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"      {c:5d} × {r}")

    summarize_rejections(rejection_details_internal, "INTERNAL")
    summarize_rejections(rejection_details_swing, "SWING")

    # ── Show first 5 internal breaks with full detail ─────────────────────────
    print(f"\n[6] First 10 INTERNAL breaks — detailed trace")
    for d in rejection_details_internal[:10]:
        print(f"    Break idx={d['break_idx']} ts={d['break_ts'][:19]}  "
              f"{d['direction'].upper():8s} {d['break_type'].upper():5s}  "
              f"price={d['break_price']:.2f}  "
              f"search=[{d['search_start']}, {d['search_end']})  len={d['range_len']}  "
              f"→ {d.get('ob_created') or d.get('rejection', '?')}")

    # ── Show first 5 swing breaks ─────────────────────────────────────────────
    print(f"\n[7] First 10 SWING breaks — detailed trace")
    for d in rejection_details_swing[:10]:
        print(f"    Break idx={d['break_idx']} ts={d['break_ts'][:19]}  "
              f"{d['direction'].upper():8s} {d['break_type'].upper():5s}  "
              f"price={d['break_price']:.2f}  "
              f"search=[{d['search_start']}, {d['search_end']})  len={d['range_len']}  "
              f"→ {d.get('ob_created') or d.get('rejection', '?')}")

    # ── Check the get_confirmed_pivots() issue ─────────────────────────────────
    print(f"\n[8] get_confirmed_pivots() check")
    print(f"    internal_highs: {len(int_highs)}  internal_lows: {len(int_lows)}")
    print(f"    swing_highs:    {len(sw_highs)}   swing_lows:    {len(sw_lows)}")
    print(f"    Total internal pivots passed to OB: {len(all_int_pivots)}")
    print(f"    Total swing pivots passed to OB:    {len(all_sw_pivots)}")
    
    # ── KEY ISSUE CHECK: does get_confirmed_pivots() return only 0 or 1 pivot? ─
    # If the detectors only track the CURRENT pivot (not history), the OB
    # _find_broken_pivot_index will only see the FINAL pivot and miss all earlier ones.
    if len(all_int_pivots) <= 2:
        print()
        print("  *** ROOT CAUSE CANDIDATE ***")
        print("  get_confirmed_pivots() returns only the CURRENT pivot pair (≤2 pivots).")
        print("  It does NOT return historical pivots.")
        print("  _find_broken_pivot_index searches this tiny list and finds nothing")
        print("  for any historical break, triggering the fallback search_start=break_idx-length.")
        print("  This means pivot_search_start ≈ break_idx - 5, giving a valid range,")
        print("  so the issue must be elsewhere.")

    # ── SECOND KEY CHECK: are breaks tracked with the pivot that was active at break time? ──
    print(f"\n[9] Pivot snapshot at break time vs get_confirmed_pivots() result")
    print(f"    StructureBreak has no pivot_index field! It only stores:")
    print(f"      .index (break candle), .price (close), .direction, .break_type")
    print(f"    _find_broken_pivot_index must search all_int_pivots/all_sw_pivots")
    print(f"    for a pivot that matches the break.")
    print()
    # Show what get_confirmed_pivots returns
    print(f"    int_highs returned: {[(p.index, float(p.price)) for p in int_highs]}")
    print(f"    int_lows  returned: {[(p.index, float(p.price)) for p in int_lows]}")
    print(f"    sw_highs  returned: {[(p.index, float(p.price)) for p in sw_highs]}")
    print(f"    sw_lows   returned: {[(p.index, float(p.price)) for p in sw_lows]}")

    # ── Check first internal break vs available pivots ─────────────────────────
    if int_breaks:
        brk0 = int_breaks[0]
        print(f"\n[10] First internal break: idx={brk0.index} dir={brk0.direction.value} price={float(brk0.price):.2f}")
        print(f"     Searching for pivot {'high' if brk0.direction==TrendDirection.BULLISH else 'low'} before idx={brk0.index} in {len(all_int_pivots)} pivots")
        found_pivot = None
        for p in reversed(all_int_pivots):
            if brk0.direction == TrendDirection.BULLISH:
                if p.is_high and p.index < brk0.index and brk0.price > p.price:
                    found_pivot = p
                    break
            else:
                if not p.is_high and p.index < brk0.index and brk0.price < p.price:
                    found_pivot = p
                    break
        if found_pivot:
            print(f"     Found pivot: idx={found_pivot.index} price={float(found_pivot.price):.2f}")
            print(f"     Range would be [{found_pivot.index}, {brk0.index}) = {brk0.index - found_pivot.index} candles")
        else:
            print(f"     NO PIVOT FOUND — search will use fallback: break_idx - length = {brk0.index - 5}")
            print(f"     Fallback range: [{brk0.index-5}, {brk0.index}) = 5 candles")
            print()
            print("  *** ROOT CAUSE: get_confirmed_pivots() only returns FINAL state.")
            print("  *** At the time of the FIRST break, the detector hasn't stored that")
            print("  *** break's pivot in the final state. The list only has the LAST pivot.")

    # ── REAL PIVOT HISTORY CHECK ───────────────────────────────────────────────
    # Replay and collect ALL pivots as they are created
    print(f"\n[11] Re-running with full pivot history collection")
    int_det2 = StructureDetector(StructureConfig(5, StructureType.INTERNAL))
    all_int_pivots_hist = []  # all pivots in order of creation
    all_int_breaks2 = []

    for i, pc in enumerate(parsed):
        prev_ph = int_det2.state.pivot_high
        prev_pl = int_det2.state.pivot_low
        breaks = int_det2.process_candle(pc, i)
        
        # Check if a new pivot was set
        curr_ph = int_det2.state.pivot_high
        curr_pl = int_det2.state.pivot_low
        if curr_ph and (not prev_ph or curr_ph.index != prev_ph.index):
            all_int_pivots_hist.append(PivotPoint(
                index=curr_ph.index,
                timestamp=curr_ph.timestamp,
                price=curr_ph.price,
                is_high=True,
                candle=curr_ph.candle,
            ))
        if curr_pl and (not prev_pl or curr_pl.index != prev_pl.index):
            all_int_pivots_hist.append(PivotPoint(
                index=curr_pl.index,
                timestamp=curr_pl.timestamp,
                price=curr_pl.price,
                is_high=False,
                candle=curr_pl.candle,
            ))
        
        all_int_breaks2.extend(breaks)

    print(f"    Total pivots created during replay: {len(all_int_pivots_hist)}")
    print(f"    vs get_confirmed_pivots() result:   {len(all_int_pivots)}")

    # Now try OB detection with full pivot history
    ob_created_with_hist = 0
    hist_rejections = {}
    for brk in all_int_breaks2:
        search_start = ob_detector._find_broken_pivot_index(
            brk, all_int_pivots_hist, all_sw_pivots, "internal"
        )
        search_end = brk.index
        if search_start >= 0 and search_start < search_end:
            ob = ob_detector._create_order_block_from_break(
                parsed_candles=parsed,
                break_event=brk,
                structure_type="internal",
                internal_pivots=all_int_pivots_hist,
                swing_pivots=all_sw_pivots,
            )
            if ob:
                ob_created_with_hist += 1
            else:
                hist_rejections["create returned None"] = hist_rejections.get("create returned None", 0) + 1
        elif search_start < 0:
            hist_rejections["search_start < 0"] = hist_rejections.get("search_start < 0", 0) + 1
        else:
            hist_rejections[f"range invalid (start={search_start}>=end={search_end})"] = hist_rejections.get(f"range invalid", 0) + 1

    print(f"\n    OBs created with full pivot history: {ob_created_with_hist}")
    print(f"    Rejection breakdown:")
    for r, c in sorted(hist_rejections.items(), key=lambda x: -x[1]):
        print(f"      {c:5d} × {r}")

    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    run_diagnostic()
