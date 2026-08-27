"""
TEMPORARY RESEARCH SCRIPT — FORENSIC MANUAL RECONSTRUCTION OF BTC 1H SETUP
August 25-27, 2026 (BTCUSD.P on Delta Exchange India)
DO NOT COMMIT / DO NOT PUSH.
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

workspace_dir = Path(r"c:\Users\durge\OneDrive\Desktop\Antigravity App\QuantEdge AI")
sys.path.insert(0, str(workspace_dir / "engine" / "src"))

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups
from quantedge.smc.volatility import parse_candles_with_volatility
from quantedge.smc.structure import StructureConfig, StructureDetector, StructureType
from quantedge.smc.order_blocks import OrderBlockConfig, detect_order_blocks_streaming

IST_TZ = timezone(timedelta(hours=5, minutes=30))

root = workspace_dir / "data" / "canonical" / "delta_exchange_india"
candles = load_canonical_full_history(root, "BTCUSD")

# Let's inspect candles from index 19570 to 19596
print("="*120)
print(f"{'Idx':<6} {'Timestamp (UTC)':<20} {'Timestamp (IST)':<20} {'Open':>9} {'High':>9} {'Low':>9} {'Close':>9} {'Range':>8} {'Candle Type':<15}")
print("="*120)

for i in range(19570, 19597):
    c = candles[i]
    ts_utc = c.timestamp.strftime("%Y-%m-%d %H:%M")
    ts_ist = c.timestamp.astimezone(IST_TZ).strftime("%Y-%m-%d %H:%M")
    o, h, l, cl = float(c.open), float(c.high), float(c.low), float(c.close)
    rng = h - l
    c_type = "BULLISH (Green)" if cl >= o else "BEARISH (Red)"
    print(f"{i:<6} {ts_utc:<20} {ts_ist:<20} {o:9.1f} {h:9.1f} {l:9.1f} {cl:9.1f} {rng:8.1f} {c_type:<15}")

# Now let's trace structure breaks and pivot points streaming candle by candle
print("\n" + "="*120)
print("STREAMING SMC STRUCTURE DETECTION AROUND THIS SETUP")
print("="*120)

parsed = parse_candles_with_volatility(candles, atr_period=200, atr_multiplier=2.0)
int_cfg = StructureConfig(5, StructureType.INTERNAL)
sw_cfg = StructureConfig(50, StructureType.SWING)

int_det = StructureDetector(int_cfg)
sw_det = StructureDetector(sw_cfg)

for i in range(19570, 19597):
    pc = parsed[i]
    ibrk = int_det.process_candle(pc, i)
    sbrk = sw_det.process_candle(pc, i)
    ts_ist = candles[i].timestamp.astimezone(IST_TZ).strftime("%Y-%m-%d %H:%M")
    
    brk_info = []
    if ibrk:
        for b in ibrk:
            brk_info.append(f"INT_{b.type.value}_{b.direction.value}(pivot_idx={b.pivot_index}, break_p={b.broken_price:.1f})")
    if sbrk:
        for b in sbrk:
            brk_info.append(f"SW_{b.type.value}_{b.direction.value}(pivot_idx={b.pivot_index}, break_p={b.broken_price:.1f})")
    
    piv_info = []
    if int_det.state.pivot_high:
        piv_info.append(f"IntPH={int_det.state.pivot_high.price:.1f}(bar {int_det.state.pivot_high.index})")
    if int_det.state.pivot_low:
        piv_info.append(f"IntPL={int_det.state.pivot_low.price:.1f}(bar {int_det.state.pivot_low.index})")
        
    print(f"Bar {i} ({ts_ist}): Breaks: {brk_info if brk_info else 'None'} | Pivots: {piv_info}")
