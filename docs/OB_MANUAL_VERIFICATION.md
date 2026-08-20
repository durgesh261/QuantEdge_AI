# QuantEdge AI V2 — Order Block Manual Verification Pack

## 1. Dataset & Engine Provenance

| Parameter | Value |
|-----------|-------|
| Exchange | Delta Exchange India |
| API Symbol | BTCUSD |
| TradingView Symbol | BTCUSD.P |
| Timeframe | 1H |
| Dataset Source | `data/canonical/delta_exchange_india/BTCUSD/1h/2026.csv` |
| Dataset SHA-256 | `2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b` |
| Dataset Period | 2026-01-01T00:00:00+00:00 → 2026-08-20T00:00:00+00:00 |
| Candle Count | 5,545 |
| ATR Period | 200 |
| ATR Multiplier | 2.0 |
| Internal Length | 5 |
| Swing Length | 50 |
| OB Filter | ATR |
| OB Mitigation Rule | High/Low |
| Generation Timestamp | 2026-08-20T11:06:04Z |
| all_ob_events.csv SHA-256 | `f92b8916679ddbf070e203bb750fba49ea22f438d4c0435e2d156a3ffad7379b` |

> **Data Cutoff**: The canonical dataset ends at **{cutoff}**.
> Python OBs are only computed through this timestamp.
> Do NOT assume engine data extends to the current wall-clock time.

## 2. TradingView Free Limitation

> ⚠️ **LuxAlgo on TradingView Free** does not preserve historical mitigated OBs.
> When you scroll back to a historical candle, LuxAlgo only shows OBs that are
> **currently unmitigated** (active) relative to the most recent chart bar.
>
> Therefore:
> - 'Not visible on TradingView Free' **MUST NOT** be classified as 'Python incorrect.'
> - Only **currently-active OBs** (at the dataset cutoff) can be reliably verified
>   against TradingView Free.
> - TradingView Bar Replay (Premium only) would be required for exact historical validation.

### Manual Validation Classification

| Status | Meaning |
|--------|---------|
| MATCH | Price zone found, direction + zone match within tolerance |
| APPROXIMATE_MATCH | Direction match, price within ±1% |
| NOT_VISIBLE / CANNOT_VERIFY | OB not shown on TradingView Free (expected for mitigated) |
| PRICE_MISMATCH | OB found but price differs beyond tolerance |
| DIRECTION_MISMATCH | OB found but direction differs |
| EXTRA_PYTHON_OB | Python shows active OB, TradingView does not show it |
| OTHER | Document in notes |

## 3. All-Time OB Statistics

All statistics are computed from the full 2026 canonical dataset (5,545 candles).

| Metric | Value |
|--------|-------|
| Total OBs | 341 |
| Internal OBs | 315 |
| Swing OBs | 26 |
| Bullish OBs | 169 |
| Bearish OBs | 172 |
| Fresh (at cutoff) | 0 |
| Touched (at cutoff) | 36 |
| Invalidated (by state) | 305 |
| Active (at cutoff) | 36 |
| Invalidated (by lifecycle) | 305 |
| BOS-triggered OBs | 161 |
| CHOCH-triggered OBs | 180 |
| Average OB Height | 467.5 |
| Median OB Height | 413.0 |
| Earliest OB Created | 2026-01-03T04:00:00+00:00 |
| Latest OB Created | 2026-08-19T06:00:00+00:00 |

## 4. OB Creation by Month

| Month   | Internal | Swing | Bullish | Bearish | Fresh | Touched | Invalidated | Total |
| ------- | -------- | ----- | ------- | ------- | ----- | ------- | ----------- | ----- |
| 2026-01 | 38       | 3     | 17      | 24      | 0     | 11      | 30          | 41    |
| 2026-02 | 35       | 3     | 16      | 22      | 0     | 0       | 38          | 38    |
| 2026-03 | 45       | 5     | 23      | 27      | 0     | 0       | 50          | 50    |
| 2026-04 | 50       | 3     | 31      | 22      | 0     | 0       | 53          | 53    |
| 2026-05 | 38       | 4     | 18      | 24      | 0     | 10      | 32          | 42    |
| 2026-06 | 42       | 3     | 23      | 22      | 0     | 2       | 43          | 45    |
| 2026-07 | 41       | 4     | 26      | 19      | 0     | 7       | 38          | 45    |
| 2026-08 | 26       | 1     | 15      | 12      | 0     | 6       | 21          | 27    |

## 5. All Active OBs at Dataset Cutoff (2026-08-20T00:00:00+00:00)

**36 active OBs** at the dataset cutoff.

| ID  | Structure | Direction | Upper    | Lower    | Height | Created UTC               | Break | State   |
| --- | --------- | --------- | -------- | -------- | ------ | ------------------------- | ----- | ------- |
| 341 | internal  | bullish   | 64,328.0 | 64,137.5 | 190.5  | 2026-08-19T06:00:00+00:00 | bos   | touched |
| 340 | internal  | bullish   | 64,268.5 | 64,008.0 | 260.5  | 2026-08-18T13:00:00+00:00 | bos   | touched |
| 339 | internal  | bullish   | 62,936.5 | 62,687.0 | 249.5  | 2026-08-16T22:00:00+00:00 | choch | touched |
| 334 | internal  | bullish   | 62,778.0 | 62,505.0 | 273.0  | 2026-08-14T14:00:00+00:00 | bos   | touched |
| 318 | internal  | bullish   | 62,630.5 | 62,274.0 | 356.5  | 2026-08-03T08:00:00+00:00 | choch | touched |
| 316 | internal  | bullish   | 62,717.5 | 62,245.0 | 472.5  | 2026-08-01T18:00:00+00:00 | choch | touched |
| 287 | internal  | bullish   | 62,085.5 | 61,812.0 | 273.5  | 2026-07-13T18:00:00+00:00 | choch | touched |
| 288 | swing     | bullish   | 62,085.5 | 61,812.0 | 273.5  | 2026-07-13T18:00:00+00:00 | bos   | touched |
| 280 | internal  | bullish   | 62,105.5 | 61,672.5 | 433.0  | 2026-07-09T02:00:00+00:00 | choch | touched |
| 278 | internal  | bullish   | 62,029.5 | 61,303.5 | 726.0  | 2026-07-06T13:00:00+00:00 | choch | touched |
| 274 | internal  | bullish   | 61,565.5 | 61,235.0 | 330.5  | 2026-07-03T00:00:00+00:00 | bos   | touched |
| 273 | internal  | bullish   | 60,292.5 | 60,086.5 | 206.0  | 2026-07-02T07:00:00+00:00 | choch | touched |
| 270 | internal  | bullish   | 58,662.0 | 58,300.0 | 362.0  | 2026-07-01T12:00:00+00:00 | choch | touched |
| 226 | internal  | bearish   | 71,781.5 | 71,444.0 | 337.5  | 2026-06-01T17:00:00+00:00 | bos   | touched |
| 225 | internal  | bearish   | 74,078.0 | 73,627.0 | 451.0  | 2026-06-01T00:00:00+00:00 | bos   | touched |
| 223 | internal  | bearish   | 74,257.5 | 73,999.0 | 258.5  | 2026-05-31T01:00:00+00:00 | choch | touched |
| 224 | swing     | bearish   | 74,257.5 | 73,999.0 | 258.5  | 2026-05-31T01:00:00+00:00 | bos   | touched |
| 217 | internal  | bearish   | 76,158.5 | 75,541.0 | 617.5  | 2026-05-27T12:00:00+00:00 | bos   | touched |
| 207 | internal  | bearish   | 78,180.0 | 77,846.5 | 333.5  | 2026-05-21T08:00:00+00:00 | choch | touched |
| 208 | swing     | bearish   | 78,180.0 | 77,846.5 | 333.5  | 2026-05-21T08:00:00+00:00 | bos   | touched |
| 202 | internal  | bearish   | 78,452.5 | 78,192.0 | 260.5  | 2026-05-17T21:00:00+00:00 | choch | touched |
| 200 | internal  | bearish   | 79,202.5 | 79,032.0 | 170.5  | 2026-05-16T00:00:00+00:00 | bos   | touched |
| 199 | internal  | bearish   | 80,972.5 | 80,519.5 | 453.0  | 2026-05-15T07:00:00+00:00 | choch | touched |
| 195 | internal  | bearish   | 82,100.0 | 81,790.0 | 310.0  | 2026-05-11T18:00:00+00:00 | choch | touched |
| 194 | swing     | bearish   | 82,449.0 | 82,020.5 | 428.5  | 2026-05-10T23:00:00+00:00 | bos   | touched |
| 41  | internal  | bearish   | 84,493.0 | 83,768.0 | 725.0  | 2026-01-30T21:00:00+00:00 | choch | touched |
| 39  | internal  | bearish   | 84,708.5 | 84,408.0 | 300.5  | 2026-01-29T23:00:00+00:00 | bos   | touched |
| 38  | internal  | bearish   | 88,468.0 | 87,874.5 | 593.5  | 2026-01-29T08:00:00+00:00 | bos   | touched |
| 29  | swing     | bearish   | 91,004.5 | 90,425.5 | 579.0  | 2026-01-23T18:00:00+00:00 | bos   | touched |
| 22  | internal  | bearish   | 91,415.0 | 91,183.0 | 232.0  | 2026-01-20T11:00:00+00:00 | bos   | touched |
| 21  | internal  | bearish   | 93,390.5 | 92,956.0 | 434.5  | 2026-01-19T16:00:00+00:00 | bos   | touched |
| 20  | internal  | bearish   | 95,500.0 | 95,365.0 | 135.0  | 2026-01-18T22:00:00+00:00 | bos   | touched |
| 18  | internal  | bearish   | 95,626.0 | 95,387.5 | 238.5  | 2026-01-17T15:00:00+00:00 | bos   | touched |
| 19  | internal  | bearish   | 95,626.0 | 95,387.5 | 238.5  | 2026-01-17T15:00:00+00:00 | bos   | touched |
| 17  | internal  | bearish   | 95,815.5 | 95,538.5 | 277.0  | 2026-01-16T08:00:00+00:00 | bos   | touched |
| 16  | internal  | bearish   | 97,165.0 | 96,517.5 | 647.5  | 2026-01-15T09:00:00+00:00 | choch | touched |

## 6. Most Recent 50 OB Creation Events

Sorted newest first. Use these timestamps to navigate TradingView.

| ID  | Structure | Direction | Upper    | Lower    | Height | Created UTC               | Break | State       | Active |
| --- | --------- | --------- | -------- | -------- | ------ | ------------------------- | ----- | ----------- | ------ |
| 341 | internal  | bullish   | 64,328.0 | 64,137.5 | 190.5  | 2026-08-19T06:00:00+00:00 | bos   | touched     | ✓      |
| 340 | internal  | bullish   | 64,268.5 | 64,008.0 | 260.5  | 2026-08-18T13:00:00+00:00 | bos   | touched     | ✓      |
| 339 | internal  | bullish   | 62,936.5 | 62,687.0 | 249.5  | 2026-08-16T22:00:00+00:00 | choch | touched     | ✓      |
| 338 | internal  | bearish   | 63,376.5 | 63,114.0 | 262.5  | 2026-08-16T16:00:00+00:00 | choch | invalidated | ✗      |
| 337 | internal  | bullish   | 62,989.0 | 62,935.0 | 54.0   | 2026-08-16T10:00:00+00:00 | choch | invalidated | ✗      |
| 336 | internal  | bearish   | 63,139.0 | 62,994.5 | 144.5  | 2026-08-16T02:00:00+00:00 | choch | invalidated | ✗      |
| 335 | internal  | bullish   | 63,103.5 | 63,003.5 | 100.0  | 2026-08-15T15:00:00+00:00 | choch | invalidated | ✗      |
| 334 | internal  | bullish   | 62,778.0 | 62,505.0 | 273.0  | 2026-08-14T14:00:00+00:00 | bos   | touched     | ✓      |
| 333 | internal  | bearish   | 63,620.0 | 63,451.5 | 168.5  | 2026-08-13T22:00:00+00:00 | bos   | invalidated | ✗      |
| 332 | internal  | bearish   | 63,998.0 | 63,654.0 | 344.0  | 2026-08-13T05:00:00+00:00 | bos   | invalidated | ✗      |
| 331 | internal  | bearish   | 64,230.5 | 64,086.0 | 144.5  | 2026-08-12T11:00:00+00:00 | choch | invalidated | ✗      |
| 330 | internal  | bullish   | 63,818.0 | 63,584.5 | 233.5  | 2026-08-12T06:00:00+00:00 | choch | invalidated | ✗      |
| 329 | internal  | bearish   | 64,475.5 | 64,265.0 | 210.5  | 2026-08-11T12:00:00+00:00 | choch | invalidated | ✗      |
| 328 | internal  | bullish   | 64,028.5 | 63,828.0 | 200.5  | 2026-08-11T06:00:00+00:00 | choch | invalidated | ✗      |
| 326 | internal  | bearish   | 65,355.5 | 65,179.5 | 176.0  | 2026-08-10T07:00:00+00:00 | choch | invalidated | ✗      |
| 327 | swing     | bearish   | 65,355.5 | 65,179.5 | 176.0  | 2026-08-10T07:00:00+00:00 | bos   | invalidated | ✗      |
| 325 | internal  | bullish   | 64,803.0 | 64,706.0 | 97.0   | 2026-08-09T03:00:00+00:00 | choch | invalidated | ✗      |
| 324 | internal  | bearish   | 65,169.5 | 64,991.0 | 178.5  | 2026-08-08T14:00:00+00:00 | choch | invalidated | ✗      |
| 323 | internal  | bullish   | 65,053.5 | 64,889.5 | 164.0  | 2026-08-08T03:00:00+00:00 | bos   | invalidated | ✗      |
| 322 | internal  | bullish   | 64,347.0 | 64,134.0 | 213.0  | 2026-08-07T04:00:00+00:00 | choch | invalidated | ✗      |
| 321 | internal  | bearish   | 64,978.0 | 64,701.5 | 276.5  | 2026-08-06T05:00:00+00:00 | choch | invalidated | ✗      |
| 320 | internal  | bullish   | 64,393.5 | 63,852.5 | 541.0  | 2026-08-05T13:00:00+00:00 | bos   | invalidated | ✗      |
| 319 | internal  | bullish   | 63,825.5 | 63,434.5 | 391.0  | 2026-08-04T10:00:00+00:00 | bos   | invalidated | ✗      |
| 318 | internal  | bullish   | 62,630.5 | 62,274.0 | 356.5  | 2026-08-03T08:00:00+00:00 | choch | touched     | ✓      |
| 317 | internal  | bearish   | 63,786.0 | 63,351.5 | 434.5  | 2026-08-02T22:00:00+00:00 | choch | invalidated | ✗      |
| 316 | internal  | bullish   | 62,717.5 | 62,245.0 | 472.5  | 2026-08-01T18:00:00+00:00 | choch | touched     | ✓      |
| 315 | internal  | bearish   | 63,106.5 | 63,042.5 | 64.0   | 2026-08-01T14:00:00+00:00 | bos   | invalidated | ✗      |
| 314 | internal  | bearish   | 65,070.0 | 64,717.0 | 353.0  | 2026-07-30T23:00:00+00:00 | choch | invalidated | ✗      |
| 313 | swing     | bearish   | 65,149.0 | 64,668.5 | 480.5  | 2026-07-30T13:00:00+00:00 | bos   | invalidated | ✗      |
| 312 | internal  | bullish   | 64,163.0 | 63,887.0 | 276.0  | 2026-07-30T05:00:00+00:00 | choch | invalidated | ✗      |
| 311 | internal  | bearish   | 64,713.5 | 64,359.0 | 354.5  | 2026-07-29T09:00:00+00:00 | choch | invalidated | ✗      |
| 310 | internal  | bullish   | 63,956.5 | 63,576.0 | 380.5  | 2026-07-29T04:00:00+00:00 | bos   | invalidated | ✗      |
| 309 | internal  | bullish   | 63,539.0 | 63,270.0 | 269.0  | 2026-07-28T10:00:00+00:00 | choch | invalidated | ✗      |
| 308 | internal  | bearish   | 65,076.5 | 64,767.5 | 309.0  | 2026-07-27T17:00:00+00:00 | bos   | invalidated | ✗      |
| 306 | internal  | bearish   | 65,720.0 | 65,385.0 | 335.0  | 2026-07-27T06:00:00+00:00 | choch | invalidated | ✗      |
| 307 | swing     | bearish   | 65,720.0 | 65,385.0 | 335.0  | 2026-07-27T06:00:00+00:00 | choch | invalidated | ✗      |
| 305 | internal  | bullish   | 64,683.0 | 64,611.5 | 71.5   | 2026-07-26T20:00:00+00:00 | bos   | invalidated | ✗      |
| 304 | internal  | bullish   | 64,435.5 | 64,264.5 | 171.0  | 2026-07-26T06:00:00+00:00 | bos   | invalidated | ✗      |
| 303 | internal  | bullish   | 64,407.0 | 64,239.0 | 168.0  | 2026-07-25T21:00:00+00:00 | bos   | invalidated | ✗      |
| 302 | internal  | bullish   | 63,980.0 | 63,773.0 | 207.0  | 2026-07-25T08:00:00+00:00 | choch | invalidated | ✗      |
| 301 | internal  | bearish   | 64,273.5 | 64,120.0 | 153.5  | 2026-07-24T19:00:00+00:00 | bos   | invalidated | ✗      |
| 300 | internal  | bearish   | 65,786.0 | 65,344.5 | 441.5  | 2026-07-24T07:00:00+00:00 | bos   | invalidated | ✗      |
| 299 | internal  | bearish   | 66,291.5 | 66,026.5 | 265.0  | 2026-07-23T00:00:00+00:00 | bos   | invalidated | ✗      |
| 298 | internal  | bearish   | 66,713.0 | 66,510.0 | 203.0  | 2026-07-22T00:00:00+00:00 | choch | invalidated | ✗      |
| 297 | internal  | bullish   | 65,261.5 | 65,019.0 | 242.5  | 2026-07-20T19:00:00+00:00 | bos   | invalidated | ✗      |
| 296 | internal  | bullish   | 64,205.0 | 63,742.0 | 463.0  | 2026-07-20T06:00:00+00:00 | choch | invalidated | ✗      |
| 295 | internal  | bearish   | 65,018.0 | 64,476.0 | 542.0  | 2026-07-20T01:00:00+00:00 | choch | invalidated | ✗      |
| 294 | internal  | bullish   | 63,980.0 | 63,899.5 | 80.5   | 2026-07-18T05:00:00+00:00 | choch | invalidated | ✗      |
| 293 | swing     | bullish   | 63,162.5 | 62,512.5 | 650.0  | 2026-07-17T13:00:00+00:00 | bos   | invalidated | ✗      |
| 292 | internal  | bearish   | 64,881.5 | 64,292.0 | 589.5  | 2026-07-16T15:00:00+00:00 | bos   | invalidated | ✗      |

## 7. Top Manual Verification Targets

These are the most useful recent active OBs for manual TradingView comparison.
Each OB appears once with its category tags.

**23 unique targets** selected from:
- Latest 10 active internal OBs
- Latest 10 active swing OBs
- Latest 10 active bullish OBs
- Latest 10 active bearish OBs

| ID  | Structure | Direction | Upper    | Lower    | Height | Created UTC               | Break | State   | Categories                      |
| --- | --------- | --------- | -------- | -------- | ------ | ------------------------- | ----- | ------- | ------------------------------- |
| 341 | internal  | bullish   | 64,328.0 | 64,137.5 | 190.5  | 2026-08-19T06:00:00+00:00 | bos   | touched | latest_bullish, latest_internal |
| 340 | internal  | bullish   | 64,268.5 | 64,008.0 | 260.5  | 2026-08-18T13:00:00+00:00 | bos   | touched | latest_bullish, latest_internal |
| 339 | internal  | bullish   | 62,936.5 | 62,687.0 | 249.5  | 2026-08-16T22:00:00+00:00 | choch | touched | latest_bullish, latest_internal |
| 334 | internal  | bullish   | 62,778.0 | 62,505.0 | 273.0  | 2026-08-14T14:00:00+00:00 | bos   | touched | latest_bullish, latest_internal |
| 318 | internal  | bullish   | 62,630.5 | 62,274.0 | 356.5  | 2026-08-03T08:00:00+00:00 | choch | touched | latest_bullish, latest_internal |
| 316 | internal  | bullish   | 62,717.5 | 62,245.0 | 472.5  | 2026-08-01T18:00:00+00:00 | choch | touched | latest_bullish, latest_internal |
| 287 | internal  | bullish   | 62,085.5 | 61,812.0 | 273.5  | 2026-07-13T18:00:00+00:00 | choch | touched | latest_bullish, latest_internal |
| 288 | swing     | bullish   | 62,085.5 | 61,812.0 | 273.5  | 2026-07-13T18:00:00+00:00 | bos   | touched | latest_bullish, latest_swing    |
| 280 | internal  | bullish   | 62,105.5 | 61,672.5 | 433.0  | 2026-07-09T02:00:00+00:00 | choch | touched | latest_bullish, latest_internal |
| 278 | internal  | bullish   | 62,029.5 | 61,303.5 | 726.0  | 2026-07-06T13:00:00+00:00 | choch | touched | latest_bullish, latest_internal |
| 274 | internal  | bullish   | 61,565.5 | 61,235.0 | 330.5  | 2026-07-03T00:00:00+00:00 | bos   | touched | latest_internal                 |
| 226 | internal  | bearish   | 71,781.5 | 71,444.0 | 337.5  | 2026-06-01T17:00:00+00:00 | bos   | touched | latest_bearish                  |
| 225 | internal  | bearish   | 74,078.0 | 73,627.0 | 451.0  | 2026-06-01T00:00:00+00:00 | bos   | touched | latest_bearish                  |
| 224 | swing     | bearish   | 74,257.5 | 73,999.0 | 258.5  | 2026-05-31T01:00:00+00:00 | bos   | touched | latest_bearish, latest_swing    |
| 223 | internal  | bearish   | 74,257.5 | 73,999.0 | 258.5  | 2026-05-31T01:00:00+00:00 | choch | touched | latest_bearish                  |
| 217 | internal  | bearish   | 76,158.5 | 75,541.0 | 617.5  | 2026-05-27T12:00:00+00:00 | bos   | touched | latest_bearish                  |
| 208 | swing     | bearish   | 78,180.0 | 77,846.5 | 333.5  | 2026-05-21T08:00:00+00:00 | bos   | touched | latest_bearish, latest_swing    |
| 207 | internal  | bearish   | 78,180.0 | 77,846.5 | 333.5  | 2026-05-21T08:00:00+00:00 | choch | touched | latest_bearish                  |
| 202 | internal  | bearish   | 78,452.5 | 78,192.0 | 260.5  | 2026-05-17T21:00:00+00:00 | choch | touched | latest_bearish                  |
| 200 | internal  | bearish   | 79,202.5 | 79,032.0 | 170.5  | 2026-05-16T00:00:00+00:00 | bos   | touched | latest_bearish                  |
| 199 | internal  | bearish   | 80,972.5 | 80,519.5 | 453.0  | 2026-05-15T07:00:00+00:00 | choch | touched | latest_bearish                  |
| 194 | swing     | bearish   | 82,449.0 | 82,020.5 | 428.5  | 2026-05-10T23:00:00+00:00 | bos   | touched | latest_swing                    |
| 29  | swing     | bearish   | 91,004.5 | 90,425.5 | 579.0  | 2026-01-23T18:00:00+00:00 | bos   | touched | latest_swing                    |

> See `validation/ob_manual_verification/verification_checklist.md` for the fillable checklist.

## 8. Generated Files

| File | Description |
|------|-------------|
| `validation/ob_manual_verification/all_ob_events.csv` | Every OB created (all-time, all states) |
| `validation/ob_manual_verification/active_ob_snapshot.csv` | Active OBs at dataset cutoff only |
| `validation/ob_manual_verification/recent_ob_events.csv` | Most recent 50 OB creation events |
| `validation/ob_manual_verification/latest_ob_summary.json` | Machine-readable summary + stats |
| `validation/ob_manual_verification/verification_checklist.md` | Fillable manual TV checklist |
| `docs/OB_MANUAL_VERIFICATION.md` | This document |

## 9. Duplication Notes

Some price zones appear in **both internal and swing** OB lists.
This is by design — internal and swing OBs are formed by different structure break types
and carry different trading significance.
The engine correctly tracks them as separate events.
They are NOT silently merged in any output file.

If two rows share identical `upper_price`/`lower_price` but differ in `structure_type`,
they represent the same price zone detected at two different structural levels.

---

## PHASE 3D MANUAL OB VERIFICATION STATUS

| Item | Status |
|------|--------|
| Python OB inventory | ✅ COMPLETE |
| Delta Exchange India BTCUSD canonical dataset | ✅ VERIFIED |
| Binance or proxy data | ✅ NOT USED |
| All-time OB history | ✅ GENERATED |
| Latest active OB report | ✅ GENERATED |
| TradingView exact historical validation | ❌ NOT CLAIMED |
| TradingView Free limitation | ✅ DOCUMENTED |
| Phase 4 strategy development | 🔒 NOT STARTED |

> **Phase 4 readiness**: PENDING MANUAL REVIEW
> Complete the verification checklist (`verification_checklist.md`) before starting Phase 4.

---

*Generated by `engine/generate_ob_manual_verification.py`*  
*Generation timestamp: 2026-08-20T11:06:04Z*  
*Dataset SHA-256: 2000fe264d7a0c8e69265969c4d9d508aaf86ac2c9f1cbdd1b16a7d3e573831b*  