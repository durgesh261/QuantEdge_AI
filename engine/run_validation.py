"""
Phase 3A Real Market Validation Script.

Runs historical replay for BTCUSD.P, ETHUSD.P, SOLUSD.P, XRPUSD.P
and reports full OB statistics.

Data source: Binance 1H 2024 (NOT Delta Exchange)

Run from engine/ directory:
    python run_validation.py
"""

import sys
import csv
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent / "src"))

from quantedge.market_data.models import Candle, Timeframe, MarketDataSource
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureDetector, StructureConfig, StructureType
from quantedge.smc.order_blocks import detect_order_blocks_streaming, OrderBlockConfig
from quantedge.smc.models import PivotPoint

DATA_ROOT = Path(__file__).parent / "data" / "historical"
SYMBOLS = ["BTCUSD.P", "ETHUSD.P", "SOLUSD.P", "XRPUSD.P"]
ATR_PERIOD = 200
ATR_MULT = 2.0
INT_LEN = 5
SW_LEN = 50

print("=" * 70)
print("PHASE 3A REAL MARKET VALIDATION")
print("Data: Binance 1H 2024 (NOT Delta Exchange)")
print(f"ATR period={ATR_PERIOD}, multiplier={ATR_MULT}")
print(f"Internal length={INT_LEN}, Swing length={SW_LEN}")
print("=" * 70)


def load_csv(path: Path) -> list:
    candles = []
    sym = path.parts[-3]
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            candles.append(Candle(
                symbol=sym,
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


def run_pipeline(symbol: str, candles: list) -> dict:
    """Run the full SMC+OB pipeline for one symbol."""
    parsed = parse_candles_with_volatility(candles, atr_period=ATR_PERIOD, atr_multiplier=ATR_MULT)

    # -- Internal stream
    int_det = StructureDetector(StructureConfig(INT_LEN, StructureType.INTERNAL))
    int_breaks = []
    int_pivots = []
    prev_ph = None
    prev_pl = None
    for i, pc in enumerate(parsed):
        brks = int_det.process_candle(pc, i)
        ph = int_det.state.pivot_high
        pl = int_det.state.pivot_low
        if ph and (prev_ph is None or ph.index != prev_ph):
            int_pivots.append(PivotPoint(ph.index, ph.timestamp, ph.price, True, ph.candle))
            prev_ph = ph.index
        if pl and (prev_pl is None or pl.index != prev_pl):
            int_pivots.append(PivotPoint(pl.index, pl.timestamp, pl.price, False, pl.candle))
            prev_pl = pl.index
        int_breaks.extend(brks)

    # -- Swing stream
    sw_det = StructureDetector(StructureConfig(SW_LEN, StructureType.SWING))
    sw_breaks = []
    sw_pivots = []
    prev_ph = None
    prev_pl = None
    for i, pc in enumerate(parsed):
        brks = sw_det.process_candle(pc, i)
        ph = sw_det.state.pivot_high
        pl = sw_det.state.pivot_low
        if ph and (prev_ph is None or ph.index != prev_ph):
            sw_pivots.append(PivotPoint(ph.index, ph.timestamp, ph.price, True, ph.candle))
            prev_ph = ph.index
        if pl and (prev_pl is None or pl.index != prev_pl):
            sw_pivots.append(PivotPoint(pl.index, pl.timestamp, pl.price, False, pl.candle))
            prev_pl = pl.index
        sw_breaks.extend(brks)

    # -- OB detection
    _cfg = OrderBlockConfig(
        internal_length=INT_LEN,
        swing_length=SW_LEN,
        atr_period=ATR_PERIOD,
        atr_multiplier=ATR_MULT,
    )
    # Separate calls so we can attribute OBs to structure stream
    int_obs = detect_order_blocks_streaming(
        parsed_candles=parsed,
        internal_breaks=int_breaks,
        swing_breaks=[],
        internal_pivots=int_pivots,
        swing_pivots=sw_pivots,
        config=_cfg,
    )
    sw_obs = detect_order_blocks_streaming(
        parsed_candles=parsed,
        internal_breaks=[],
        swing_breaks=sw_breaks,
        internal_pivots=int_pivots,
        swing_pivots=sw_pivots,
        config=_cfg,
    )
    obs = int_obs + sw_obs
    bull_obs = [ob for ob in obs if ob.type == "BULLISH"]
    bear_obs = [ob for ob in obs if ob.type == "BEARISH"]

    int_bos = sum(1 for b in int_breaks if b.break_type.value == "bos")
    int_choch = sum(1 for b in int_breaks if b.break_type.value == "choch")
    sw_bos = sum(1 for b in sw_breaks if b.break_type.value == "bos")
    sw_choch = sum(1 for b in sw_breaks if b.break_type.value == "choch")

    return {
        "symbol": symbol,
        "candles": len(candles),
        "int_breaks": len(int_breaks),
        "int_bos": int_bos,
        "int_choch": int_choch,
        "sw_breaks": len(sw_breaks),
        "sw_bos": sw_bos,
        "sw_choch": sw_choch,
        "int_obs": len(int_obs),
        "sw_obs": len(sw_obs),
        "bull_obs": len(bull_obs),
        "bear_obs": len(bear_obs),
        "first_5_int_obs": int_obs[:5],
        "first_5_sw_obs": sw_obs[:3],
        "all_obs": obs,
        "int_pivots": len(int_pivots),
        "sw_pivots": len(sw_pivots),
    }


all_results = {}
for symbol in SYMBOLS:
    csv_file = DATA_ROOT / symbol / "1h" / "2024.csv"
    if not csv_file.exists():
        print(f"\n[SKIP] {symbol}: {csv_file} not found")
        continue
    print(f"\nProcessing {symbol}...")
    candles = load_csv(csv_file)
    result = run_pipeline(symbol, candles)
    all_results[symbol] = result
    print(f"  Loaded {result['candles']} candles")
    print(f"  Internal: {result['int_breaks']} breaks ({result['int_bos']} BOS, {result['int_choch']} CHOCH), {result['int_pivots']} pivots")
    print(f"  Swing:    {result['sw_breaks']} breaks ({result['sw_bos']} BOS, {result['sw_choch']} CHOCH), {result['sw_pivots']} pivots")
    print(f"  Internal OBs: {result['int_obs']}  Swing OBs: {result['sw_obs']}")
    print(f"  Bullish OBs:  {result['bull_obs']}  Bearish OBs: {result['bear_obs']}")

print("\n\n" + "=" * 70)
print("SUMMARY TABLE")
print("=" * 70)
print(f"{'Symbol':<12} {'Candles':>8} {'Int Breaks':>12} {'Sw Breaks':>10} {'Int OBs':>8} {'Sw OBs':>8} {'Bull':>6} {'Bear':>6}")
print("-" * 70)
for sym, r in all_results.items():
    print(f"{sym:<12} {r['candles']:>8} {r['int_breaks']:>12} {r['sw_breaks']:>10} {r['int_obs']:>8} {r['sw_obs']:>8} {r['bull_obs']:>6} {r['bear_obs']:>6}")
print("=" * 70)

# First OBs detail per symbol
def print_obs(obs_list, label, sym):
    if not obs_list:
        print(f"  {sym} [{label}]: No OBs")
        return
    print(f"\n  {sym} — First {len(obs_list)} [{label}] OBs:")
    print(f"  {'#':>2}  {'Type':<8} {'FmtIdx':>6} {'FmtTS':>21}  {'BrkIdx':>6}  {'Top':>12}  {'Bot':>12}")
    print("  " + "-" * 72)
    for j, ob in enumerate(obs_list, 1):
        fmt_ts = ob.formation_candle.timestamp.strftime("%Y-%m-%d %H:%M") if ob.formation_candle else "?"
        print(f"  {j:>2}  {ob.type:<8} {ob.formation_index:>6} {fmt_ts:>21}  {ob.break_index:>6}  {float(ob.top_price):>12.2f}  {float(ob.bottom_price):>12.2f}")

for sym, r in all_results.items():
    print(f"\n{sym} OB Detail:")
    print_obs(r["first_5_int_obs"], "INTERNAL", sym)
    print_obs(r["first_5_sw_obs"], "SWING", sym)

print("\n\nDone.")
