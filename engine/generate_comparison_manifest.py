"""
SMC Validation: Generate Python event tables for TradingView/LuxAlgo comparison.

Produces per-window event CSV files under validation/manual/.

Data: Binance 1H 2024  (NOT Delta Exchange)
Settings: internal_length=5, swing_length=50, ATR=200, mult=2.0

Run from repo root:
    python engine/generate_comparison_manifest.py
"""

import sys
import csv
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

REPO = Path(__file__).parent.parent   # workspace root (one level above engine/)
ENGINE = Path(__file__).parent        # engine/
sys.path.insert(0, str(ENGINE / "src"))

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import detect_order_blocks_streaming, OrderBlockConfig
from quantedge.smc.models import PivotPoint, TrendDirection, BreakType

# ─── Validation Windows ───────────────────────────────────────────────────────
# Same 5 date windows applied to all 4 symbols.
# All timestamps UTC.  Windows chosen to cover distinct 2024 BTC market regimes.

WINDOWS = [
    {
        "id": "W1",
        "category": "bearish_to_bullish_transition",
        "description": "Early 2024 recovery — BTC from ~$40k Jan dip back to $46k",
        "start": datetime(2024, 1, 3, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W2",
        "category": "bullish_trend",
        "description": "BTC bull run to ATH — $52k to $73.8k",
        "start": datetime(2024, 2, 15, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2024, 3, 14, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W3",
        "category": "bullish_to_bearish_transition",
        "description": "BTC ATH ~$73.8k and immediate reversal",
        "start": datetime(2024, 3, 13, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2024, 4, 1, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W4",
        "category": "bearish_trend",
        "description": "BTC post-ATH correction — $71k to $57k",
        "start": datetime(2024, 4, 8, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2024, 5, 1, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W5",
        "category": "ranging_consolidation",
        "description": "BTC consolidation ~$60-67k range",
        "start": datetime(2024, 5, 15, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2024, 6, 15, 0, 0, tzinfo=timezone.utc),
    },
]

SYMBOLS = ["BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P"]
DATA_ROOT = ENGINE / "data" / "historical"
OUT_ROOT  = REPO / "validation" / "manual"

ATR_PERIOD  = 200
ATR_MULT    = 2.0
INT_LEN     = 5
SW_LEN      = 50


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_csv(path: Path, symbol: str) -> List[Candle]:
    candles = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            candles.append(Candle(
                symbol=symbol,
                timeframe=Timeframe.H1,
                timestamp=ts,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row.get("volume", "0")),
                source=MarketDataSource.HISTORICAL,
            ))
    return candles


def run_full_pipeline(candles: List[Candle]):
    """
    Run full SMC + OB pipeline on candle list.
    Returns (parsed, int_breaks, sw_breaks, int_pivots, sw_pivots, int_obs, sw_obs)
    plus per-candle state history.
    """
    parsed = parse_candles_with_volatility(
        candles, atr_period=ATR_PERIOD, atr_multiplier=ATR_MULT
    )

    int_det = StructureDetector(StructureConfig(INT_LEN, StructureType.INTERNAL))
    sw_det  = StructureDetector(StructureConfig(SW_LEN,  StructureType.SWING))

    int_breaks, sw_breaks = [], []
    int_pivots, sw_pivots = [], []
    prev_int_ph = prev_int_pl = None
    prev_sw_ph  = prev_sw_pl  = None

    # per-candle state snapshots
    states = []  # list of dict

    for i, pc in enumerate(parsed):
        int_brks = int_det.process_candle(pc, i)
        sw_brks  = sw_det.process_candle(pc, i)

        # Track internal pivots
        iph = int_det.state.pivot_high
        ipl = int_det.state.pivot_low
        if iph and iph.index != prev_int_ph:
            int_pivots.append(PivotPoint(iph.index, iph.timestamp, iph.price, True,  iph.candle))
            prev_int_ph = iph.index
        if ipl and ipl.index != prev_int_pl:
            int_pivots.append(PivotPoint(ipl.index, ipl.timestamp, ipl.price, False, ipl.candle))
            prev_int_pl = ipl.index

        # Track swing pivots
        sph = sw_det.state.pivot_high
        spl = sw_det.state.pivot_low
        if sph and sph.index != prev_sw_ph:
            sw_pivots.append(PivotPoint(sph.index, sph.timestamp, sph.price, True,  sph.candle))
            prev_sw_ph = sph.index
        if spl and spl.index != prev_sw_pl:
            sw_pivots.append(PivotPoint(spl.index, spl.timestamp, spl.price, False, spl.candle))
            prev_sw_pl = spl.index

        int_breaks.extend(int_brks)
        sw_breaks.extend(sw_brks)

        snap = {
            "idx": i,
            "ts":  pc.original.timestamp,
            "close": float(pc.original.close),
            "int_trend": int_det.state.trend.value,
            "sw_trend":  sw_det.state.trend.value,
            "int_breaks": int_brks,
            "sw_breaks":  sw_brks,
            "new_int_ph": (iph.index == prev_int_ph) if iph else False,
            "new_sw_ph":  (sph.index == prev_sw_ph)  if sph else False,
            "new_int_pl": (ipl.index == prev_int_pl) if ipl else False,
            "new_sw_pl":  (spl.index == prev_sw_pl)  if spl else False,
        }
        states.append(snap)

    # OB detection (separate streams so we know structure_type)
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

    return parsed, int_breaks, sw_breaks, int_pivots, sw_pivots, int_obs, sw_obs, states


def events_in_window(
    states, int_breaks, sw_breaks, int_pivots, sw_pivots, int_obs, sw_obs,
    win_start: datetime, win_end: datetime
) -> List[Dict]:
    """
    Extract all events (pivots, breaks, OBs) that fall within [win_start, win_end).
    Returns list of event dicts sorted by candle index.
    """
    evts = []

    # Pivots
    for p in int_pivots:
        ts = p.timestamp
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({
                "idx": p.index, "ts": ts,
                "type": "PIVOT_HIGH" if p.is_high else "PIVOT_LOW",
                "stream": "internal",
                "level": float(p.price),
                "direction": "", "break_type": "",
                "ob_type": "", "ob_top": "", "ob_bot": "", "ob_src_idx": "",
            })

    for p in sw_pivots:
        ts = p.timestamp
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({
                "idx": p.index, "ts": ts,
                "type": "PIVOT_HIGH" if p.is_high else "PIVOT_LOW",
                "stream": "swing",
                "level": float(p.price),
                "direction": "", "break_type": "",
                "ob_type": "", "ob_top": "", "ob_bot": "", "ob_src_idx": "",
            })

    # Structure breaks
    for brk in int_breaks:
        ts = brk.timestamp
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({
                "idx": brk.index, "ts": ts,
                "type": brk.break_type.value.upper(),
                "stream": "internal",
                "level": float(brk.price),
                "direction": brk.direction.value,
                "break_type": brk.break_type.value,
                "ob_type": "", "ob_top": "", "ob_bot": "", "ob_src_idx": "",
            })

    for brk in sw_breaks:
        ts = brk.timestamp
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({
                "idx": brk.index, "ts": ts,
                "type": brk.break_type.value.upper(),
                "stream": "swing",
                "level": float(brk.price),
                "direction": brk.direction.value,
                "break_type": brk.break_type.value,
                "ob_type": "", "ob_top": "", "ob_bot": "", "ob_src_idx": "",
            })

    # OBs — keyed by break_index timestamp
    for ob in int_obs:
        ts = ob.formation_candle.timestamp
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({
                "idx": ob.formation_index, "ts": ts,
                "type": "OB_" + ob.type,
                "stream": "internal",
                "level": "",
                "direction": ob.type,
                "break_type": ob.break_type.value,
                "ob_type": ob.type,
                "ob_top": float(ob.top_price),
                "ob_bot": float(ob.bottom_price),
                "ob_src_idx": ob.formation_index,
            })

    for ob in sw_obs:
        ts = ob.formation_candle.timestamp
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({
                "idx": ob.formation_index, "ts": ts,
                "type": "OB_" + ob.type,
                "stream": "swing",
                "level": "",
                "direction": ob.type,
                "break_type": ob.break_type.value,
                "ob_type": ob.type,
                "ob_top": float(ob.top_price),
                "ob_bot": float(ob.bottom_price),
                "ob_src_idx": ob.formation_index,
            })

    evts.sort(key=lambda e: (e["idx"], e["type"]))
    return evts


def write_window_csv(path: Path, events: List[Dict]) -> None:
    fieldnames = [
        "idx", "timestamp_utc", "event_type", "stream",
        "direction", "level",
        "ob_type", "ob_top", "ob_bot", "ob_src_idx",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in events:
            w.writerow({
                "idx": e["idx"],
                "timestamp_utc": e["ts"].strftime("%Y-%m-%d %H:%M"),
                "event_type": e["type"],
                "stream": e["stream"],
                "direction": e["direction"],
                "level": e["level"],
                "ob_type": e["ob_type"],
                "ob_top": e["ob_top"],
                "ob_bot": e["ob_bot"],
                "ob_src_idx": e["ob_src_idx"],
            })


# ─── Main ─────────────────────────────────────────────────────────────────────

manifest_rows = []
summary = {"note": "Binance 1H 2024 — NOT Delta Exchange", "windows": [], "counts": {}}

for sym in SYMBOLS:
    csv_file = DATA_ROOT / sym / "1h" / "2024.csv"
    if not csv_file.exists():
        print(f"[SKIP] {sym}: {csv_file} not found")
        continue

    print(f"\n{'='*60}")
    print(f"Processing {sym}...")
    candles = load_csv(csv_file, sym)
    print(f"  Loaded {len(candles)} candles")

    parsed, int_breaks, sw_breaks, int_pivots, sw_pivots, int_obs, sw_obs, states = \
        run_full_pipeline(candles)

    sym_dir = OUT_ROOT / sym
    sym_dir.mkdir(parents=True, exist_ok=True)

    sym_counts = {
        "int_breaks": len(int_breaks),
        "sw_breaks": len(sw_breaks),
        "int_obs": len(int_obs),
        "sw_obs": len(sw_obs),
    }
    summary["counts"][sym] = sym_counts

    for win in WINDOWS:
        wid  = win["id"]
        wcat = win["category"]
        wstart, wend = win["start"], win["end"]

        evts = events_in_window(
            states, int_breaks, sw_breaks, int_pivots, sw_pivots,
            int_obs, sw_obs, wstart, wend
        )

        # Count events in window
        int_bos   = sum(1 for e in evts if e["type"] == "BOS"   and e["stream"] == "internal")
        int_choch = sum(1 for e in evts if e["type"] == "CHOCH" and e["stream"] == "internal")
        sw_bos    = sum(1 for e in evts if e["type"] == "BOS"   and e["stream"] == "swing")
        sw_choch  = sum(1 for e in evts if e["type"] == "CHOCH" and e["stream"] == "swing")
        int_obs_w = sum(1 for e in evts if e["type"].startswith("OB_") and e["stream"] == "internal")
        sw_obs_w  = sum(1 for e in evts if e["type"].startswith("OB_") and e["stream"] == "swing")
        candles_in_win = sum(
            1 for c in candles
            if wstart <= (c.timestamp if c.timestamp.tzinfo else c.timestamp.replace(tzinfo=timezone.utc)) < wend
        )

        out_csv = sym_dir / f"{wid}_{wcat}.csv"
        write_window_csv(out_csv, evts)

        row = {
            "symbol": sym,
            "window": wid,
            "category": wcat,
            "description": win["description"],
            "start": wstart.strftime("%Y-%m-%d %H:%M UTC"),
            "end": wend.strftime("%Y-%m-%d %H:%M UTC"),
            "candle_count": candles_in_win,
            "int_bos": int_bos,
            "int_choch": int_choch,
            "sw_bos": sw_bos,
            "sw_choch": sw_choch,
            "int_obs": int_obs_w,
            "sw_obs": sw_obs_w,
            "total_events": len(evts),
            "csv": str(out_csv.relative_to(REPO)),
        }
        manifest_rows.append(row)
        summary["windows"].append(row)

        print(f"  {wid} [{wcat[:30]}]: "
              f"{candles_in_win} candles | "
              f"int {int_bos}BOS/{int_choch}CHOCH | "
              f"sw {sw_bos}BOS/{sw_choch}CHOCH | "
              f"OBs int={int_obs_w}/sw={sw_obs_w} | "
              f"{len(evts)} events -> {out_csv.name}")

# Write master manifest JSON
manifest_path = OUT_ROOT / "manifest.json"
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, default=str)
print(f"\nManifest written: {manifest_path}")

# Write master manifest CSV
master_csv = OUT_ROOT / "manifest.csv"
if manifest_rows:
    with open(master_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        w.writeheader()
        w.writerows(manifest_rows)
print(f"Master CSV:       {master_csv}")

# Print summary table
print(f"\n\n{'='*80}")
print("VALIDATION WINDOWS SUMMARY")
print(f"{'='*80}")
print(f"{'Symbol':<12} {'Win':<4} {'Category':<30} {'Candles':>7} "
      f"{'IntBOS':>7} {'IntCHO':>7} {'SwBOS':>6} {'SwCHO':>6} "
      f"{'IntOBs':>7} {'SwOBs':>6}")
print("-"*80)
for r in manifest_rows:
    print(f"{r['symbol']:<12} {r['window']:<4} {r['category'][:30]:<30} "
          f"{r['candle_count']:>7} {r['int_bos']:>7} {r['int_choch']:>7} "
          f"{r['sw_bos']:>6} {r['sw_choch']:>6} "
          f"{r['int_obs']:>7} {r['sw_obs']:>6}")
print(f"{'='*80}")
print(f"\nTotal windows: {len(manifest_rows)} "
      f"({len(WINDOWS)} windows × {len(SYMBOLS)} symbols)")
