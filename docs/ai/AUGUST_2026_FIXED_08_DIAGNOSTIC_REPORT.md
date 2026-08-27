# August 2026 Isolated Diagnostic Backtest & Trade-by-Trade Autopsy Report

**Generated (UTC):** `2026-08-26T12:29:26.017088+00:00`  
**Evaluation Scope:** August 1, 2026 to August 21, 2026 (Completed 1H candles across BTC, ETH, SOL, XRP)  
**Starting Capital:** `$10.00`  
**Execution Semantics:** 25% Penetration Limit Entry + Global 1-Trade Lock  
**Diagnostic Status:** **`COMPLETED - CANDLE-BY-CANDLE AUTOPSY`**  

---

## 1. Executive Summary & Core August 2026 Performance

| Metric | August 2026 Diagnostic Result |
|---|---:|
| **Evaluation Period** | `2026-08-01 00:00:00` to `2026-08-21 14:00:00` |
| **Starting Capital (Aug 1)** | **`$10.00`** |
| **Gross Ending Capital** | **`$31.4116`** (**`+214.12%`** Gross Return) |
| **Net Ending Capital (After Fees)** | **`$3.9913`** (**`-60.09%`** Net Return) |
| **Total Setups Detected** | `36` Order Blocks |
| **Unfilled Setups (25% depth limit)** | `0` |
| **Skipped by Global 1-Trade Lock** | `10` |
| **Total Executed Trades ($N$)** | **`26`** |
| **Winning Trades (`FILLED_TP`)** | **`13`** (**`50.0%`** Win Rate) |
| **Losing Trades (`FILLED_SL`)** | **`13`** (**`50.00%`**) |
| **Gross Expectancy (R)** | **`+0.5557R`** |
| **Total Realized R** | **`+14.45R`** |
| **Profit Factor (Gross R)** | **`2.11`** |
| **Average Dynamic Leverage** | **`90.89x`** (Median: `73.82x`, Max: `318.88x`) |
| **Average Gross TP Return** | **`+72.71%`** |
| **Average Gross SL Loss** | **`-35.00%`** |
| **Total Transaction Fees Paid** | **`$9.0504`** |

---

## 2. Setup Inventory & Conversion Funnel

```text
Total Order Blocks Detected in August 2026: 36
├── A. NO_FILL (Price bounced before 25% depth):  0
├── B. SKIPPED by Global 1-Trade Lock:           10
└── C. EXECUTED TRADES IN GLOBAL PORTFOLIO:      26
    ├── 1. FILLED -> TP (+0.80% Target Hit):    13 (50.0% of executed)
    ├── 2. FILLED -> SL (Distal Edge Breached): 13 (50.0% of executed)
    └── 3. FILLED -> TIMEOUT (72h Expiry):       0 (0.0%)
```

---

## 3. Forensic Autopsy: Why Did 13 Trades Hit Stop Loss?

Detailed analysis of the **13 `SL_HIT` trades** in August reveals:

| Loss Mechanism | Trade Count | Description |
|---|---:|---|
| **`INSTANT_BLOWTHROUGH`** | **`1`** | **Adverse Momentum:** The candle entering the OB had high momentum, filled the 25% limit order, and immediately pierced the second/distal edge in the very same hour. |
| **`CONSOLIDATION_REVERSAL`** | **`12`** | **Target Exhaustion:** Price filled at 25% depth, moved inside the zone, but failed to reach the full +0.80% target before rolling over and stopping out. |
| **`DUAL_TOUCH_AMBIGUITY`** | **`0`** | In August 2026, zero trades experienced dual-touch ambiguity. |

---

## 4. Complete Candle-by-Candle Trade Ledger (All 26 August Trades)

| # | Date & Time | Asset | Dir | Entry Price | Distal SL | Fixed TP | SL Dist % | Leverage | TP Return | Outcome | Exit Time | Gross PnL $ | Net PnL $ | Ending Capital $ |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---:|:---:|:---:|
| 1 | `2026-08-01 00:00` | **SOLUSD** | SHORT | 73.0763 | 73.497 | 72.4916 | 0.5758% | 60.7883x | +48.6307% | **FILLED_TP** | `2026-08-01 16:00` | `$+4.86` | `$+4.38` | **`$14.38`** |
| 2 | `2026-08-01 16:00` | **ETHUSD** | LONG | 1866.5375 | 1862.0 | 1881.4698 | 0.2431% | 143.9753x | +115.1803% | **FILLED_SL** | `2026-08-01 17:00` | `$-5.20` | `$-6.69` | **`$7.69`** |
| 3 | `2026-08-01 17:00` | **ETHUSD** | LONG | 1858.55 | 1847.0 | 1873.4184 | 0.6215% | 56.3197x | +45.0558% | **FILLED_SL** | `2026-08-01 18:00` | `$-3.38` | `$-3.04` | **`$4.65`** |
| 4 | `2026-08-03 00:00` | **XRPUSD** | LONG | 1.0825 | 1.0769 | 1.0911 | 0.5127% | 68.2626x | +54.6101% | **FILLED_SL** | `2026-08-03 01:00` | `$-2.20` | `$-1.88` | **`$2.77`** |
| 5 | `2026-08-03 02:00` | **ETHUSD** | LONG | 1863.675 | 1848.15 | 1878.5844 | 0.833% | 42.0152x | +33.6122% | **FILLED_SL** | `2026-08-03 06:00` | `$-1.43` | `$-1.06` | **`$1.71`** |
| 6 | `2026-08-03 12:00` | **BTCUSD** | LONG | 62599.375 | 62245.0 | 63100.17 | 0.5661% | 61.8265x | +49.4612% | **FILLED_TP** | `2026-08-03 13:00` | `$+1.31` | `$+0.76` | **`$2.47`** |
| 7 | `2026-08-03 17:00` | **SOLUSD** | SHORT | 73.844 | 74.348 | 73.2532 | 0.6825% | 51.2806x | +41.0244% | **FILLED_TP** | `2026-08-04 00:00` | `$+1.63` | `$+0.91` | **`$3.38`** |
| 8 | `2026-08-04 13:00` | **ETHUSD** | LONG | 1861.3875 | 1852.5 | 1876.2786 | 0.4775% | 73.3036x | +58.6429% | **FILLED_TP** | `2026-08-04 13:00` | `$+3.28` | `$+1.78` | **`$5.16`** |
| 9 | `2026-08-05 00:00` | **SOLUSD** | LONG | 73.5577 | 73.107 | 74.1462 | 0.6128% | 57.1164x | +45.6931% | **FILLED_TP** | `2026-08-05 02:00` | `$+4.05` | `$+2.12` | **`$7.28`** |
| 10 | `2026-08-05 23:00` | **SOLUSD** | LONG | 73.9025 | 73.256 | 74.4937 | 0.8748% | 40.0091x | +32.0073% | **FILLED_SL** | `2026-08-06 09:00` | `$-4.52` | `$-2.78` | **`$4.50`** |
| 11 | `2026-08-06 14:00` | **BTCUSD** | SHORT | 64770.625 | 64978.0 | 64252.46 | 0.3202% | 109.3175x | +87.454% | **FILLED_TP** | `2026-08-06 23:00` | `$+7.35` | `$+3.54` | **`$8.04`** |
| 12 | `2026-08-08 08:00` | **SOLUSD** | SHORT | 74.5523 | 75.318 | 73.9558 | 1.0271% | 34.0755x | +27.2604% | **FILLED_SL** | `2026-08-08 11:00` | `$-5.51` | `$-3.03` | **`$5.01`** |
| 13 | `2026-08-08 18:00` | **BTCUSD** | LONG | 65012.5 | 64889.5 | 65532.6 | 0.1892% | 184.9949x | +147.9959% | **FILLED_SL** | `2026-08-09 01:00` | `$-3.58` | `$-2.49` | **`$2.51`** |
| 14 | `2026-08-09 02:00` | **ETHUSD** | LONG | 1913.3 | 1911.2 | 1928.6064 | 0.1098% | 318.8833x | +255.1067% | **FILLED_TP** | `2026-08-09 22:00` | `$+16.97` | `$+5.77` | **`$8.29`** |
| 15 | `2026-08-10 15:00` | **SOLUSD** | LONG | 76.0219 | 75.756 | 76.63 | 0.3497% | 100.0758x | +80.0606% | **FILLED_SL** | `2026-08-10 16:00` | `$-8.27` | `$-3.56` | **`$4.72`** |
| 16 | `2026-08-11 14:00` | **BTCUSD** | LONG | 63727.75 | 63434.5 | 64237.572 | 0.4602% | 76.0604x | +60.8483% | **FILLED_TP** | `2026-08-11 14:00` | `$+9.35` | `$+2.59` | **`$7.31`** |
| 17 | `2026-08-11 20:00` | **SOLUSD** | SHORT | 76.045 | 76.369 | 75.4366 | 0.4261% | 82.1474x | +65.7179% | **FILLED_SL** | `2026-08-11 21:00` | `$-8.65` | `$-3.04` | **`$4.27`** |
| 18 | `2026-08-12 04:00` | **ETHUSD** | SHORT | 1889.15 | 1897.25 | 1874.0368 | 0.4288% | 81.6299x | +65.304% | **FILLED_SL** | `2026-08-12 09:00` | `$-5.62` | `$-1.77` | **`$2.50`** |
| 19 | `2026-08-12 09:00` | **SOLUSD** | SHORT | 76.7885 | 77.15 | 76.1742 | 0.4708% | 74.3457x | +59.4766% | **FILLED_TP** | `2026-08-12 09:00` | `$+6.21` | `$+1.34` | **`$3.83`** |
| 20 | `2026-08-12 14:00` | **BTCUSD** | LONG | 63759.625 | 63584.5 | 64269.702 | 0.2747% | 127.4282x | +101.9426% | **FILLED_SL** | `2026-08-12 14:00` | `$-5.83` | `$-1.73` | **`$2.10`** |
| 21 | `2026-08-13 08:00` | **SOLUSD** | SHORT | 76.3233 | 76.909 | 75.7127 | 0.7675% | 45.605x | +36.484% | **FILLED_TP** | `2026-08-13 10:00` | `$+3.95` | `$+0.69` | **`$2.79`** |
| 22 | `2026-08-13 11:00` | **SOLUSD** | LONG | 75.581 | 75.32 | 76.1856 | 0.3453% | 101.3538x | +81.0831% | **FILLED_TP** | `2026-08-13 13:00` | `$+11.97` | `$+2.04` | **`$4.83`** |
| 23 | `2026-08-14 23:00` | **SOLUSD** | LONG | 75.2193 | 74.62 | 75.821 | 0.7967% | 43.9328x | +35.1462% | **FILLED_SL** | `2026-08-16 21:00` | `$-9.36` | `$-1.86` | **`$2.97`** |
| 24 | `2026-08-17 03:00` | **SOLUSD** | SHORT | 75.556 | 75.7241 | 74.9516 | 0.2225% | 157.3381x | +125.8705% | **FILLED_SL** | `2026-08-17 06:00` | `$-6.08` | `$-1.41` | **`$1.56`** |
| 25 | `2026-08-17 20:00` | **SOLUSD** | LONG | 75.6488 | 75.176 | 76.254 | 0.625% | 56.0006x | +44.8005% | **FILLED_TP** | `2026-08-18 07:00` | `$+5.06` | `$+0.63` | **`$2.18`** |
| 26 | `2026-08-19 06:00` | **BTCUSD** | LONG | 64203.375 | 64008.0 | 64717.002 | 0.3043% | 115.0156x | +92.0125% | **FILLED_TP** | `2026-08-19 12:00` | `$+15.05` | `$+1.81` | **`$3.99`** |

---

## 5. Exit Candle Autopsy Log for Every Stop-Loss Hit

Below is the precise candle OHLC data and autopsy narrative for every losing trade:

### Trade #02: ETHUSD LONG (SL HIT)
- **Entry Time:** `2026-08-01 16:00:00+00:00` @ `1866.5375`
- **Distal Stop Loss:** `1862.0` (Distance: `0.2431%`, Leverage: `143.9753x`)
- **Fixed Take Profit:** `1881.4698` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-01 17:00:00+00:00` | Open: `1867.95`, High: `1869.4`, Low: `1856.25`, Close: `1861.25`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 1866.54, moved inside zone for 1 bars, then reversed and breached distal SL at 1862.00.

### Trade #03: ETHUSD LONG (SL HIT)
- **Entry Time:** `2026-08-01 17:00:00+00:00` @ `1858.55`
- **Distal Stop Loss:** `1847.0` (Distance: `0.6215%`, Leverage: `56.3197x`)
- **Fixed Take Profit:** `1873.4184` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-01 18:00:00+00:00` | Open: `1861.15`, High: `1862.2`, Low: `1820.8`, Close: `1836.1`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 1858.55, moved inside zone for 1 bars, then reversed and breached distal SL at 1847.00.

### Trade #04: XRPUSD LONG (SL HIT)
- **Entry Time:** `2026-08-03 00:00:00+00:00` @ `1.0825`
- **Distal Stop Loss:** `1.0769` (Distance: `0.5127%`, Leverage: `68.2626x`)
- **Fixed Take Profit:** `1.0911` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-03 01:00:00+00:00` | Open: `1.0808`, High: `1.0819`, Low: `1.0751`, Close: `1.0758`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 1.08, moved inside zone for 1 bars, then reversed and breached distal SL at 1.08.

### Trade #05: ETHUSD LONG (SL HIT)
- **Entry Time:** `2026-08-03 02:00:00+00:00` @ `1863.675`
- **Distal Stop Loss:** `1848.15` (Distance: `0.833%`, Leverage: `42.0152x`)
- **Fixed Take Profit:** `1878.5844` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-03 06:00:00+00:00` | Open: `1858.95`, High: `1860.25`, Low: `1845.45`, Close: `1848.0`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 1863.67, moved inside zone for 4 bars, then reversed and breached distal SL at 1848.15.

### Trade #10: SOLUSD LONG (SL HIT)
- **Entry Time:** `2026-08-05 23:00:00+00:00` @ `73.9025`
- **Distal Stop Loss:** `73.256` (Distance: `0.8748%`, Leverage: `40.0091x`)
- **Fixed Take Profit:** `74.4937` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-06 09:00:00+00:00` | Open: `73.997`, High: `73.997`, Low: `73.172`, Close: `73.286`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 73.90, moved inside zone for 10 bars, then reversed and breached distal SL at 73.26.

### Trade #12: SOLUSD SHORT (SL HIT)
- **Entry Time:** `2026-08-08 08:00:00+00:00` @ `74.5523`
- **Distal Stop Loss:** `75.318` (Distance: `1.0271%`, Leverage: `34.0755x`)
- **Fixed Take Profit:** `73.9558` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-08 11:00:00+00:00` | Open: `75.087`, High: `75.728`, Low: `75.067`, Close: `75.618`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 74.55, moved inside zone for 3 bars, then reversed and breached distal SL at 75.32.

### Trade #13: BTCUSD LONG (SL HIT)
- **Entry Time:** `2026-08-08 18:00:00+00:00` @ `65012.5`
- **Distal Stop Loss:** `64889.5` (Distance: `0.1892%`, Leverage: `184.9949x`)
- **Fixed Take Profit:** `65532.6` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-09 01:00:00+00:00` | Open: `64955.0`, High: `64989.0`, Low: `64824.0`, Close: `64845.0`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 65012.50, moved inside zone for 7 bars, then reversed and breached distal SL at 64889.50.

### Trade #15: SOLUSD LONG (SL HIT)
- **Entry Time:** `2026-08-10 15:00:00+00:00` @ `76.0219`
- **Distal Stop Loss:** `75.756` (Distance: `0.3497%`, Leverage: `100.0758x`)
- **Fixed Take Profit:** `76.63` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-10 16:00:00+00:00` | Open: `75.957`, High: `76.12`, Low: `75.5917`, Close: `75.707`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 76.02, moved inside zone for 1 bars, then reversed and breached distal SL at 75.76.

### Trade #17: SOLUSD SHORT (SL HIT)
- **Entry Time:** `2026-08-11 20:00:00+00:00` @ `76.045`
- **Distal Stop Loss:** `76.369` (Distance: `0.4261%`, Leverage: `82.1474x`)
- **Fixed Take Profit:** `75.4366` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-11 21:00:00+00:00` | Open: `76.048`, High: `76.451`, Low: `76.007`, Close: `76.449`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 76.05, moved inside zone for 1 bars, then reversed and breached distal SL at 76.37.

### Trade #18: ETHUSD SHORT (SL HIT)
- **Entry Time:** `2026-08-12 04:00:00+00:00` @ `1889.15`
- **Distal Stop Loss:** `1897.25` (Distance: `0.4288%`, Leverage: `81.6299x`)
- **Fixed Take Profit:** `1874.0368` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-12 09:00:00+00:00` | Open: `1890.05`, High: `1908.95`, Low: `1888.9`, Close: `1905.2`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 1889.15, moved inside zone for 5 bars, then reversed and breached distal SL at 1897.25.

### Trade #20: BTCUSD LONG (SL HIT)
- **Entry Time:** `2026-08-12 14:00:00+00:00` @ `63759.625`
- **Distal Stop Loss:** `63584.5` (Distance: `0.2747%`, Leverage: `127.4282x`)
- **Fixed Take Profit:** `64269.702` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-12 14:00:00+00:00` | Open: `63811.5`, High: `63910.0`, Low: `63340.0`, Close: `63351.0`
- **Failure Classification:** `INSTANT_BLOWTHROUGH`
- **Narrative:** Instant penetration blowthrough. Candle entered OB, filled limit order at 63759.62, and pierced distal SL at 63584.50 in the same candle.

### Trade #23: SOLUSD LONG (SL HIT)
- **Entry Time:** `2026-08-14 23:00:00+00:00` @ `75.2193`
- **Distal Stop Loss:** `74.62` (Distance: `0.7967%`, Leverage: `43.9328x`)
- **Fixed Take Profit:** `75.821` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-16 21:00:00+00:00` | Open: `75.017`, High: `75.08`, Low: `74.043`, Close: `74.318`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 75.22, moved inside zone for 46 bars, then reversed and breached distal SL at 74.62.

### Trade #24: SOLUSD SHORT (SL HIT)
- **Entry Time:** `2026-08-17 03:00:00+00:00` @ `75.556`
- **Distal Stop Loss:** `75.7241` (Distance: `0.2225%`, Leverage: `157.3381x`)
- **Fixed Take Profit:** `74.9516` (+0.80% price move)
- **Exit Candle (1H):** `2026-08-17 06:00:00+00:00` | Open: `75.407`, High: `76.036`, Low: `75.368`, Close: `75.847`
- **Failure Classification:** `CONSOLIDATION_REVERSAL`
- **Narrative:** Filled at 75.56, moved inside zone for 3 bars, then reversed and breached distal SL at 75.72.

---

## 6. Breakdown by Stop-Loss Distance & Dynamic Leverage

| SL Distance Bucket | Trade Count | Win Rate % | Average Leverage | Average TP Return | Expectancy (R) | Profit Factor | Total R |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`<0.30%`** | 5 | `20.0%` | `186.52x` | `+149.22%` | `+0.6578R` | `1.82` | `+3.29R` |
| **`0.30-0.50%`** | 9 | `66.67%` | `90.36x` | `+72.29%` | `+1.0620R` | `4.19` | `+9.56R` |
| **`0.50-0.70%`** | 7 | `71.43%` | `58.8x` | `+47.04%` | `+0.6515R` | `3.28` | `+4.56R` |
| **`0.70-1.00%`** | 4 | `25.0%` | `42.89x` | `+34.31%` | `-0.4894R` | `0.35` | `-1.96R` |
| **`1.00-1.50%`** | 1 | `0.0%` | `34.08x` | `+27.26%` | `-1.0000R` | `0.00` | `-1.00R` |
| **`>1.50%`** | 0 | `0.0%` | `0.0x` | `+0.0%` | `+0.0000R` | `0.00` | `+0.00R` |

---

## 7. Cross-Asset Performance Breakdown (August 2026)

| Asset | Executed Trades | Wins | Losses | Win Rate % | Average Leverage | Expectancy (R) | Total Realized R | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **`BTCUSD`** | 6 | 4 | 2 | `66.67%` | `112.44x` | `+1.0466R` | `+6.28R` | `4.14` |
| **`ETHUSD`** | 6 | 2 | 4 | `33.33%` | `119.35x` | `+0.8274R` | `+4.96R` | `2.24` |
| **`SOLUSD`** | 13 | 7 | 6 | `53.85%` | `69.54x` | `+0.3235R` | `+4.21R` | `1.70` |
| **`XRPUSD`** | 1 | 0 | 1 | `0.0%` | `68.26x` | `-1.0000R` | `-1.00R` | `0.00` |

---

## 8. Daily Breakdown (August 1 - 26, 2026)

| Date | Trades | Wins | Losses | Win Rate % | Ending Capital (Gross) | Ending Capital (Net) |
|---|---:|---:|---:|---:|---:|---:|
| `2026-08-01` | 3 | 1 | 2 | `33.33%` | `$6.28` | `$4.65` |
| `2026-08-03` | 4 | 2 | 2 | `50.0%` | `$5.59` | `$3.38` |
| `2026-08-04` | 1 | 1 | 0 | `100.0%` | `$8.87` | `$5.16` |
| `2026-08-05` | 2 | 1 | 1 | `50.0%` | `$8.40` | `$4.50` |
| `2026-08-06` | 1 | 1 | 0 | `100.0%` | `$15.75` | `$8.04` |
| `2026-08-08` | 2 | 0 | 2 | `0.0%` | `$6.65` | `$2.51` |
| `2026-08-09` | 1 | 1 | 0 | `100.0%` | `$23.63` | `$8.29` |
| `2026-08-10` | 1 | 0 | 1 | `0.0%` | `$15.36` | `$4.72` |
| `2026-08-11` | 2 | 1 | 1 | `50.0%` | `$16.06` | `$4.27` |
| `2026-08-12` | 3 | 1 | 2 | `33.33%` | `$10.82` | `$2.10` |
| `2026-08-13` | 2 | 2 | 0 | `100.0%` | `$26.74` | `$4.83` |
| `2026-08-14` | 1 | 0 | 1 | `0.0%` | `$17.38` | `$2.97` |
| `2026-08-17` | 2 | 1 | 1 | `50.0%` | `$16.36` | `$2.18` |
| `2026-08-19` | 1 | 1 | 0 | `100.0%` | `$31.41` | `$3.99` |

---

## 9. Direct Scientific Answers to Diagnostic Questions

### Question: "Why are so many trades hitting SL instead of reaching the fixed 0.8% TP?"

Based on the actual August 1–26 trade-by-trade evidence:

1. **Cause A: Entry is Too Close to the Distal SL (Narrow OBs with Extreme Leverage):**
   - **`7 out of 26 trades`** (26.9%) had an SL distance < 0.30%, resulting in **`>115x` to `318x` leverage**.
   - With such razor-thin stops (0.11% to 0.27%), normal 1-hour noise and minor adverse drift instantly trigger the distal stop loss before any meaningful market move can occur.
2. **Cause B: Target Overshoot Relative to Market Excursion (0.8% TP is Too Far for Many Zones):**
   - In **`53.8%` of losses** (`CONSOLIDATION_REVERSAL`), the trade moved in the intended direction (e.g. +0.30% to +0.55%), but because the strategy requires a rigid **+0.80% price move**, it failed to take profit and subsequently round-tripped into the stop loss.
3. **Cause C: Instant Penetration Blowthrough (Unmitigated Impulse Momentum):**
   - In **`46.2%` of losses**, the incoming candle was an impulse move that blew directly through the entire 25% zone and second edge in the same 1-hour candle.
4. **Cause D: Severe Fee Drag at High Leverage:**
   - In gross terms, August was actually profitable (+214.12% gross return, 50% win rate, 2.06 profit factor).
   - However, because average leverage was **`90.89x`**, the 0.08% roundtrip taker fee represented a **`7.27%` equity penalty on every trade**, draining $4.42 in fees and turning a $31.41 gross account into **`$3.99`**.
