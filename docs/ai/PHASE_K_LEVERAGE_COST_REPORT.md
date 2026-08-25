# QuantEdge AI — Phase K Leverage, Tail-Risk & Cost Sensitivity Report

**Generated At (UTC)**: 2026-08-25T07:55:11.422856+00:00  

## 1. Dynamic Leverage & Liquidation Risk Analysis

- **Universe Average Leverage**: `49.8x` (Median: `47.0x`, Max: `100x`)
- **OOS AI Accepted Average Leverage**: `48.6x` (Median: `48.0x`)
- **Liquidations Before Stop-Loss**: **`0`** (Zero violations)
- **Tail-Risk Assessment**: ACCEPTABLE (0 liquidations before SL; isolated margin preserves stop barrier)

## 2. Strict Transaction Cost Stress Testing

| Scenario / Friction Level | Mean Net Expectancy | Net Profit Factor | Win Rate | Total Net Realized R | Survives Edge? |
|---|---:|---:|---:|---:|:---:|
| **Gross (Zero Fees/Slippage)** | `+0.2845R` | 1.553 | 45.95% | `+10.53R` | ✅ YES |
| **Base (0.05% Taker, 0.01% Slip, 0.01%/8h Fund)** | `+0.0909R` | 1.15 | 45.95% | `+3.36R` | ✅ YES |
| **2x Slippage (0.02% Slip)** | `+0.0607R` | 1.098 | 45.95% | `+2.25R` | ✅ YES |
| **2x Taker Fee (0.10% Fee)** | `-0.0601R` | 0.911 | 45.95% | `-2.22R` | ❌ NO |
| **Stress (2x Fee + 2x Slip + 2x Fund)** | `-0.1027R` | 0.853 | 45.95% | `-3.80R` | ❌ NO |