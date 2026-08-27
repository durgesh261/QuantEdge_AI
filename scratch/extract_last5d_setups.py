"""
Test extracting setups directly using the deterministic SMC engine on updated candles.
"""

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

from quantedge.ai.evaluation.phase_l_research import load_canonical_full_history, _find_repo_root
from quantedge.ai.evaluation.phase_i_ob_replay import build_smc_context, extract_phase_i_setups

root = _find_repo_root()
base = root / "data" / "canonical" / "delta_exchange_india"

symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]

all_setups = []

for sym in symbols:
    candles = load_canonical_full_history(base, sym)
    print(f"\n{sym}: Loaded {len(candles)} candles. Latest: {candles[-1].timestamp}")
    ctx = build_smc_context(candles)
    setups, audit = extract_phase_i_setups(candles, symbol=sym, ctx=ctx)
    print(f"  Extracted {len(setups)} total setups for {sym}")
    
    for s in setups:
        dt = datetime.fromisoformat(s.creation_time)
        if dt >= datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc):
            all_setups.append({
                "asset": sym,
                "direction": s.direction,
                "creation_time": s.creation_time,
                "entry_price": float(s.entry_price),
                "sl_price": float(s.sl_price),
                "tp_price": float(s.tp_price),
                "ob_high": float(s.ob_high),
                "ob_low": float(s.ob_low),
                "decision_bar": s.decision_bar,
            })

print(f"\nTotal Order Blocks in last 5 days (Aug 21–26): {len(all_setups)}")
for i, s in enumerate(sorted(all_setups, key=lambda x: x["creation_time"])):
    top = s["ob_high"]
    bot = s["ob_low"]
    w = top - bot
    if s["direction"] == "LONG":
        entry = top - 0.25 * w
        sl = bot
    else:
        entry = bot + 0.25 * w
        sl = top
    sl_pct = abs(entry - sl) / entry * 100.0
    lev = 0.35 / (abs(entry - sl) / entry)
    print(f"#{i+1:02d} | {s['asset']} {s['direction']} | Time: {s['creation_time'][:16]} | OB: [{bot:.2f}, {top:.2f}] | Entry: {entry:.2f} | SL: {sl:.2f} ({sl_pct:.3f}%) | Lev: {lev:.1f}x")
