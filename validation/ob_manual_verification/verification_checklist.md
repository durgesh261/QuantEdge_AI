# Manual TradingView Order Block Verification Checklist

> **Generated**: 2026-08-20T11:06:04Z
> **Data cutoff**: 2026-08-20T00:00:00+00:00 (last canonical candle)
> **Exchange**: Delta Exchange India | **Symbol**: BTCUSD.P | **TF**: 1H

## Instructions

1. Open TradingView Free → Delta Exchange India → BTCUSD.P → 1H chart
2. Load LuxAlgo Smart Money Concepts indicator with settings:
   - Swing Length: 50 | Internal Length: 5 | OB Filter: ATR | Mitigation: High/Low
3. Scroll chart to approximately the Creation Timestamp for each entry
4. Record what you see in the TradingView fields below

> ⚠️ **TradingView Free Limitation**: LuxAlgo only shows OBs that are *currently active*
> (not mitigated) in the visible window. Mitigated OBs may not be displayed.
> 'Not visible' does NOT automatically mean the Python engine is wrong.

---

## Verification Status Legend

| Code | Meaning |
|------|---------|
| MATCH | Box found, direction + zone match |
| APPROXIMATE_MATCH | Direction match, price within ±1% |
| NOT_VISIBLE / CANNOT_VERIFY | Box not shown on TradingView Free (expected for old/mitigated OBs) |
| PRICE_MISMATCH | Box found but price differs beyond tolerance |
| DIRECTION_MISMATCH | Box found but direction differs |
| EXTRA_PYTHON_OB | Python shows OB, TradingView does not (may be sub-swing level or filtered) |
| OTHER | Any other case — document in Notes |

---

## Verification Targets (23 OBs)

### OB #341 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 341 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 64,328.0 |
| Lower Price | 64,137.5 |
| OB Height | 190.5 |
| Created UTC | 2026-08-19T06:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #340 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 340 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 64,268.5 |
| Lower Price | 64,008.0 |
| OB Height | 260.5 |
| Created UTC | 2026-08-18T13:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #339 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 339 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 62,936.5 |
| Lower Price | 62,687.0 |
| OB Height | 249.5 |
| Created UTC | 2026-08-16T22:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #334 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 334 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 62,778.0 |
| Lower Price | 62,505.0 |
| OB Height | 273.0 |
| Created UTC | 2026-08-14T14:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #318 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 318 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 62,630.5 |
| Lower Price | 62,274.0 |
| OB Height | 356.5 |
| Created UTC | 2026-08-03T08:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #316 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 316 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 62,717.5 |
| Lower Price | 62,245.0 |
| OB Height | 472.5 |
| Created UTC | 2026-08-01T18:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #287 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 287 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 62,085.5 |
| Lower Price | 61,812.0 |
| OB Height | 273.5 |
| Created UTC | 2026-07-13T18:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #288 — SWING BULLISH
**Categories**: latest_bullish, latest_swing

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 288 |
| Structure | swing |
| Direction | bullish |
| Upper Price | 62,085.5 |
| Lower Price | 61,812.0 |
| OB Height | 273.5 |
| Created UTC | 2026-07-13T18:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #280 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 280 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 62,105.5 |
| Lower Price | 61,672.5 |
| OB Height | 433.0 |
| Created UTC | 2026-07-09T02:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #278 — INTERNAL BULLISH
**Categories**: latest_bullish, latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 278 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 62,029.5 |
| Lower Price | 61,303.5 |
| OB Height | 726.0 |
| Created UTC | 2026-07-06T13:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #274 — INTERNAL BULLISH
**Categories**: latest_internal

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 274 |
| Structure | internal |
| Direction | bullish |
| Upper Price | 61,565.5 |
| Lower Price | 61,235.0 |
| OB Height | 330.5 |
| Created UTC | 2026-07-03T00:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #226 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 226 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 71,781.5 |
| Lower Price | 71,444.0 |
| OB Height | 337.5 |
| Created UTC | 2026-06-01T17:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #225 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 225 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 74,078.0 |
| Lower Price | 73,627.0 |
| OB Height | 451.0 |
| Created UTC | 2026-06-01T00:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #224 — SWING BEARISH
**Categories**: latest_bearish, latest_swing

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 224 |
| Structure | swing |
| Direction | bearish |
| Upper Price | 74,257.5 |
| Lower Price | 73,999.0 |
| OB Height | 258.5 |
| Created UTC | 2026-05-31T01:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #223 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 223 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 74,257.5 |
| Lower Price | 73,999.0 |
| OB Height | 258.5 |
| Created UTC | 2026-05-31T01:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #217 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 217 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 76,158.5 |
| Lower Price | 75,541.0 |
| OB Height | 617.5 |
| Created UTC | 2026-05-27T12:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #208 — SWING BEARISH
**Categories**: latest_bearish, latest_swing

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 208 |
| Structure | swing |
| Direction | bearish |
| Upper Price | 78,180.0 |
| Lower Price | 77,846.5 |
| OB Height | 333.5 |
| Created UTC | 2026-05-21T08:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #207 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 207 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 78,180.0 |
| Lower Price | 77,846.5 |
| OB Height | 333.5 |
| Created UTC | 2026-05-21T08:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #202 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 202 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 78,452.5 |
| Lower Price | 78,192.0 |
| OB Height | 260.5 |
| Created UTC | 2026-05-17T21:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #200 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 200 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 79,202.5 |
| Lower Price | 79,032.0 |
| OB Height | 170.5 |
| Created UTC | 2026-05-16T00:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #199 — INTERNAL BEARISH
**Categories**: latest_bearish

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 199 |
| Structure | internal |
| Direction | bearish |
| Upper Price | 80,972.5 |
| Lower Price | 80,519.5 |
| OB Height | 453.0 |
| Created UTC | 2026-05-15T07:00:00+00:00 |
| Break Type | choch |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #194 — SWING BEARISH
**Categories**: latest_swing

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 194 |
| Structure | swing |
| Direction | bearish |
| Upper Price | 82,449.0 |
| Lower Price | 82,020.5 |
| OB Height | 428.5 |
| Created UTC | 2026-05-10T23:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---

### OB #29 — SWING BEARISH
**Categories**: latest_swing

**Python Engine Data** (authoritative):

| Field | Value |
|-------|-------|
| OB ID | 29 |
| Structure | swing |
| Direction | bearish |
| Upper Price | 91,004.5 |
| Lower Price | 90,425.5 |
| OB Height | 579.0 |
| Created UTC | 2026-01-23T18:00:00+00:00 |
| Break Type | bos |
| State | touched |
| Is Active | True |

**TradingView Manual Entry** (fill in):

| Field | Your Observation |
|-------|-----------------|
| Found visually? | `[ ] YES  [ ] NO` |
| Approx Upper | _(fill in)_ |
| Approx Lower | _(fill in)_ |
| Direction matches? | `[ ] YES  [ ] NO` |
| Zone location matches? | `[ ] YES  [ ] NO` |
| Status | `[ ] MATCH  [ ] APPROXIMATE_MATCH  [ ] NOT_VISIBLE  [ ] PRICE_MISMATCH  [ ] DIRECTION_MISMATCH  [ ] EXTRA_PYTHON_OB  [ ] OTHER` |
| Notes | _(fill in)_ |

---
