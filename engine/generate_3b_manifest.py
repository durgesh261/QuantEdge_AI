"""
Phase 3B: Generate Python SMC events for recent 2026 data.
Uses the same configuration as Phase 3A.

Outputs per-window event CSVs for TradingView comparison.
Note: Data is Binance USDT spot proxy — NOT Delta Exchange.

Run from repo root:
    python engine/generate_3b_manifest.py
"""

import sys
import csv
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

REPO = ENGINE.parent
DATA_ROOT = ENGINE / "data" / "historical"
OUT_ROOT  = REPO / "validation" / "phase3b"

ATR_PERIOD = 200
ATR_MULT   = 2.0
INT_LEN    = 5
SW_LEN     = 50

# ── 5 representative windows in 2026 (recent enough for TV Free) ─────────────
# All UTC. Covers recent 2026 crypto regimes.
WINDOWS_2026 = [
    {
        "id": "W2026_1",
        "category": "bearish_trend",
        "description": "Jan-Feb 2026 BTC correction from highs",
        "start": datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2026, 2, 10, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W2026_2",
        "category": "bullish_trend",
        "description": "Mar-Apr 2026 recovery rally",
        "start": datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W2026_3",
        "category": "ranging_consolidation",
        "description": "Apr-May 2026 consolidation",
        "start": datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W2026_4",
        "category": "bullish_to_bearish_transition",
        "description": "Jun 2026 peak and reversal",
        "start": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
    },
    {
        "id": "W2026_5",
        "category": "recent",
        "description": "Most recent 2026 period — Aug 2026",
        "start": datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
        "end":   datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
    },
]

SYMBOLS = ["BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P"]


def load_csv(path: Path, symbol: str):
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


def run_pipeline(candles):
    parsed = parse_candles_with_volatility(candles, atr_period=ATR_PERIOD, atr_multiplier=ATR_MULT)
    int_det = StructureDetector(StructureConfig(INT_LEN, StructureType.INTERNAL))
    sw_det  = StructureDetector(StructureConfig(SW_LEN,  StructureType.SWING))

    int_breaks, sw_breaks = [], []
    int_pivots, sw_pivots = [], []
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

        for b in int_det.process_candle(pc, i):  # already called above, breaks returned inline
            pass
        for b in sw_det.process_candle(pc, i):
            pass

    # Re-run cleanly to get breaks (process_candle returns breaks)
    int_det2 = StructureDetector(StructureConfig(INT_LEN, StructureType.INTERNAL))
    sw_det2  = StructureDetector(StructureConfig(SW_LEN,  StructureType.SWING))
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

    return parsed, int_breaks, sw_breaks, int_pivots, sw_pivots, int_obs, sw_obs


def extract_window_events(int_breaks, sw_breaks, int_pivots, sw_pivots,
                           int_obs, sw_obs, win_start, win_end):
    evts = []

    for p in int_pivots:
        ts = p.timestamp if p.timestamp.tzinfo else p.timestamp.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({"idx": p.index, "ts": ts, "event": "PIVOT_HIGH" if p.is_high else "PIVOT_LOW",
                         "stream": "internal", "price": float(p.price),
                         "ob_top": "", "ob_bot": "", "ob_dir": ""})

    for p in sw_pivots:
        ts = p.timestamp if p.timestamp.tzinfo else p.timestamp.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({"idx": p.index, "ts": ts, "event": "PIVOT_HIGH" if p.is_high else "PIVOT_LOW",
                         "stream": "swing", "price": float(p.price),
                         "ob_top": "", "ob_bot": "", "ob_dir": ""})

    for b in int_breaks:
        ts = b.timestamp if b.timestamp.tzinfo else b.timestamp.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({"idx": b.index, "ts": ts,
                         "event": b.break_type.value.upper(),
                         "stream": "internal", "price": float(b.price),
                         "ob_top": "", "ob_bot": "", "ob_dir": b.direction.value})

    for b in sw_breaks:
        ts = b.timestamp if b.timestamp.tzinfo else b.timestamp.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({"idx": b.index, "ts": ts,
                         "event": b.break_type.value.upper(),
                         "stream": "swing", "price": float(b.price),
                         "ob_top": "", "ob_bot": "", "ob_dir": b.direction.value})

    for ob in int_obs:
        ts = ob.formation_candle.timestamp if ob.formation_candle.timestamp.tzinfo else \
             ob.formation_candle.timestamp.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({"idx": ob.formation_index, "ts": ts,
                         "event": f"OB_{ob.type}", "stream": "internal", "price": "",
                         "ob_top": float(ob.top_price), "ob_bot": float(ob.bottom_price),
                         "ob_dir": ob.type})

    for ob in sw_obs:
        ts = ob.formation_candle.timestamp if ob.formation_candle.timestamp.tzinfo else \
             ob.formation_candle.timestamp.replace(tzinfo=timezone.utc)
        if win_start <= ts < win_end:
            evts.append({"idx": ob.formation_index, "ts": ts,
                         "event": f"OB_{ob.type}", "stream": "swing", "price": "",
                         "ob_top": float(ob.top_price), "ob_bot": float(ob.bottom_price),
                         "ob_dir": ob.type})

    evts.sort(key=lambda e: (e["idx"], e["event"]))
    return evts


print("Phase 3B manifest — generating 2026 window events")
print("="*60)

all_summary = []

for sym in SYMBOLS:
    csv_file = DATA_ROOT / sym / "1h" / "2026.csv"
    if not csv_file.exists():
        print(f"  [SKIP] {sym}: 2026.csv not found")
        continue

    print(f"\n{sym}...")
    candles = load_csv(csv_file, sym)
    print(f"  Loaded {len(candles)} candles ({candles[0].timestamp.date()} to {candles[-1].timestamp.date()})")

    parsed, int_breaks, sw_breaks, int_pivots, sw_pivots, int_obs, sw_obs = run_pipeline(candles)

    print(f"  Total: {len(int_breaks)} int breaks, {len(sw_breaks)} sw breaks, "
          f"{len(int_obs)} int OBs, {len(sw_obs)} sw OBs")

    sym_dir = OUT_ROOT / sym
    sym_dir.mkdir(parents=True, exist_ok=True)

    for win in WINDOWS_2026:
        wid, wcat = win["id"], win["category"]
        wstart, wend = win["start"], win["end"]

        evts = extract_window_events(
            int_breaks, sw_breaks, int_pivots, sw_pivots,
            int_obs, sw_obs, wstart, wend
        )

        int_bos   = sum(1 for e in evts if e["event"] == "BOS"   and e["stream"] == "internal")
        int_choch = sum(1 for e in evts if e["event"] == "CHOCH" and e["stream"] == "internal")
        sw_bos    = sum(1 for e in evts if e["event"] == "BOS"   and e["stream"] == "swing")
        sw_choch  = sum(1 for e in evts if e["event"] == "CHOCH" and e["stream"] == "swing")
        int_obs_w = sum(1 for e in evts if e["event"].startswith("OB_") and e["stream"] == "internal")
        sw_obs_w  = sum(1 for e in evts if e["event"].startswith("OB_") and e["stream"] == "swing")

        out_csv = sym_dir / f"{wid}_{wcat}.csv"
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            fw = csv.DictWriter(f, fieldnames=["idx", "timestamp_utc", "event", "stream",
                                                "direction_or_type", "price",
                                                "ob_top", "ob_bot"])
            fw.writeheader()
            for e in evts:
                fw.writerow({
                    "idx": e["idx"],
                    "timestamp_utc": e["ts"].strftime("%Y-%m-%d %H:%M"),
                    "event": e["event"],
                    "stream": e["stream"],
                    "direction_or_type": e["ob_dir"],
                    "price": e["price"],
                    "ob_top": e["ob_top"],
                    "ob_bot": e["ob_bot"],
                })

        row = {"symbol": sym, "window": wid, "category": wcat,
               "start": wstart.strftime("%Y-%m-%d"), "end": wend.strftime("%Y-%m-%d"),
               "int_bos": int_bos, "int_choch": int_choch,
               "sw_bos": sw_bos, "sw_choch": sw_choch,
               "int_obs": int_obs_w, "sw_obs": sw_obs_w,
               "total_events": len(evts)}
        all_summary.append(row)
        print(f"  {wid} [{wcat[:25]}]: "
              f"int {int_bos}BOS/{int_choch}CHOCH | sw {sw_bos}BOS/{sw_choch}CHOCH | "
              f"OBs i={int_obs_w}/s={sw_obs_w} | {len(evts)} events")

# Print summary table
print(f"\n{'='*80}")
print("PHASE 3B EVENT SUMMARY")
print(f"{'='*80}")
print(f"{'Symbol':<12} {'Win':<10} {'Category':<25} "
      f"{'iBOS':>5} {'iCHO':>5} {'sBOS':>5} {'sCHO':>5} {'iOBs':>5} {'sOBs':>5}")
print("-"*80)
for r in all_summary:
    print(f"{r['symbol']:<12} {r['window']:<10} {r['category'][:25]:<25} "
          f"{r['int_bos']:>5} {r['int_choch']:>5} {r['sw_bos']:>5} {r['sw_choch']:>5} "
          f"{r['int_obs']:>5} {r['sw_obs']:>5}")
print(f"{'='*80}")
print(f"\nCSVs saved to: {OUT_ROOT}")
