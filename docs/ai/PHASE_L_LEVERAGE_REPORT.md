# QuantEdge AI — Phase L Leverage, Tail-Risk & Liquidation Report

**Generated At (UTC)**: 2026-08-25T08:54:41.717548+00:00  

## 1. Dynamic Leverage Distribution

- **Universe Average Leverage**: `49.8x` (Median: `47.0x`, Max: `100x`)
- **OOS AI Accepted Average Leverage**: `44.4x` (Median: `42.0x`)
- **Liquidations Before Stop-Loss**: **`0`** (Zero violations)
- **Tail-Risk Assessment**: ACCEPTABLE (0 liquidations before SL; isolated margin preserves stop barrier)

## 2. Risk Model Invariant
Dynamic leverage $\lfloor 35.0 / \text{stop\_pct} \rfloor$ capped at $100$x allocates fixed 35% risk budget while ensuring stop loss triggers well before maintenance margin breach.