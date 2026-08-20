# Phase 3E — OB Differential Validation

> **Generated**: 2026-08-20T11:38:54Z
> **Dataset**: Delta Exchange India BTCUSD 1H | 5,545 candles
> **Dataset SHA-256**: `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b`

## Purpose

This directory contains diagnostic outputs from Phase 3E.
The goal is NOT to modify production SMC logic, but to understand
the differences between Python OB output and LuxAlgo TradingView visuals.

## Files

| File | Description |
|------|-------------|
| `ob_lifecycle_trace.csv` | Candle-by-candle lifecycle trace for latest active OBs |
| `ob_creation_diagnostics.csv` | One row per OB: production vs diagnostic state |
| `tv_ob_manual_reference_template.json` | Template to enter TradingView BLUE OB observations |
| `differential_results.json` | Summary of all diagnostic findings + known discrepancies |
| `README.md` | This file |

## Key Findings

- **Total OBs**: 341
- **Break-candle overlaps OB zone**: 99 (29.0%)
- **State discrepancies (production vs diagnostic)**: 74

## Root Cause Hypothesis

The production `_apply_lifecycle()` in `ob_snapshot_engine.py` starts checking
lifecycle for all candles **after** the formation candle timestamp.
The **break candle** (which confirmed the structure break) is the first candle
processed. If the break candle's price range overlaps the OB zone, the OB is
immediately marked `TOUCHED` — even though no genuine *retest* of the zone occurred.

This is the likely cause of the TradingView visual discrepancy:
LuxAlgo may treat the break candle as the *trigger* for OB formation,
not as a retest of the zone.

## FVG vs OB Distinction

- **BLUE zones** in LuxAlgo = Order Blocks (OBs)
- **GREEN zones** in LuxAlgo = Fair Value Gaps (FVGs)

Green FVG zones must NEVER be matched against Python OBs.
Any observation with `is_fvg: true` is automatically excluded.

## Status

```
Phase 3E status:  DIAGNOSTIC / PENDING MANUAL TV BLUE OB REFERENCES
Production SMC:   FROZEN (ZERO DIFF)
Phase 4:          NOT STARTED
```

## Next Steps

1. Fill in `tv_ob_manual_reference_template.json` with actual LuxAlgo blue OB prices
2. Re-run `generate_phase3e_diagnostics.py` with references populated
3. Review whether diagnostic lifecycle (break-candle excluded from touch) matches TV
4. Only then consider a targeted production SMC update (if warranted)