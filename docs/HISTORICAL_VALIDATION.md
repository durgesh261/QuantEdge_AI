# Historical Validation for QuantEdge LuxAlgo SMC

This document describes the historical validation infrastructure for the QuantEdge LuxAlgo SMC implementation.

## Overview

The historical validation system provides a deterministic, causal replay engine for validating the LuxAlgo SMC implementation against real historical market data. It processes candles one-by-one through the SMC engine and generates a normalized event stream that can be compared against TradingView/LuxAlgo reference outputs.

## Architecture

### Components

1. **Historical Data Provider** (`quantedge.historical.provider`)
   - `HistoricalDataProvider` - Abstract base class
   - `CsvHistoricalDataProvider` - CSV-based implementation
   - Dataset metadata and quality validation

2. **Event Models** (`quantedge.historical.events`)
   - Normalized event types: LEG_CHANGE, PIVOT_CREATED, BOS, CHOCH, ORDER_BLOCK_CREATED, ORDER_BLOCK_TOUCHED, ORDER_BLOCK_INVALIDATED, ORDER_BLOCK_USED
   - JSONL streaming output format
   - Factory functions for creating events

3. **Replay Engine** (`quantedge.historical.replay`)
   - `HistoricalReplayEngine` - Main replay engine
   - Runs Internal (length=5) and Swing (length=50) SMC streams independently
   - Generates normalized events in JSONL format
   - Tracks statistics and generates summary reports

## Data Schema

### Candle Schema (Canonical)

```csv
timestamp,open,high,low,close,volume
2024-01-01T00:00:00,50000,50100,49900,50050,1000
```

- Timestamps: ISO 8601 format (UTC)
- Prices: Decimal precision
- Volume: Decimal precision
- No missing timestamps in continuous ranges
- Timezone: UTC explicit

### Event Schema (JSONL)

Each line is a complete JSON object:

```json
{
  "event_id": "bos_BTCUSD.P_150",
  "event_type": "bos",
  "symbol": "BTCUSD.P",
  "timeframe": "1h",
  "timestamp": "2024-01-01T15:00:00",
  "candle_index": 150,
  "break_type": "bos",
  "direction": "bullish",
  "previous_trend": "ranging",
  "trend_after": "bullish",
  "pivot_price": "50250.0",
  "pivot_index": 145,
  "break_price": "50251.0",
  "structure_type": "internal"
}
```

### Supported Event Types

| Event Type | Description |
|------------|-------------|
| `leg_change` | Leg direction transition |
| `pivot_created` | Pivot high/low created at leg transition |
| `bos` | Break of Structure |
| `choch` | Change of Character |
| `order_block_created` | OB formed from structure break |
| `order_block_touched` | OB first touch (FRESH → TOUCHED) |
| `order_block_invalidated` | OB invalidated by close through boundary |
| `order_block_used` | OB marked as USED after trade |
| `dataset_start` | Dataset begin marker |
| `replay_complete` | Replay completion summary |

## Dataset Versioning

Every dataset has a unique identity:

```json
{
  "dataset_id": "BTCUSD.P_1h_a1b2c3d4e5f6",
  "symbol": "BTCUSD.P",
  "timeframe": "1h",
  "start_time": "2024-01-01T00:00:00",
  "end_time": "2024-12-31T23:00:00",
  "source": "csv",
  "downloaded_at": "2024-01-15T12:00:00",
  "file_hash": "a1b2c3d4e5f6...",
  "candle_count": 8760,
  "gaps": [],
  "quality_report": {...}
}
```

## Replay Engine

### Deterministic Replay

The engine processes candles one-by-one in strict chronological order:

```python
for i, parsed_candle in enumerate(parsed_candles):
    # Internal structure
    internal_breaks = internal_detector.process_candle(parsed_candle, i)
    
    # Swing structure
    swing_breaks = swing_detector.process_candle(parsed_candle, i)
    
    # Order Blocks (periodically)
    if i % 100 == 0:
        process_order_blocks(i)
```

### Causality Guarantees

- **No look-ahead bias**: At candle T, only data up to T is used
- **Causal event ordering**: Events emitted in timestamp order
- **Pivot before break**: Pivot must exist before it can be broken
- **Break before OB**: OB created after break from slice [pivot, break)
- **Future invariance**: Adding future candles doesn't change past events

### Raw vs Parsed Separation

| Component | Data Source |
|-----------|-------------|
| Leg detection | RAW OHLC |
| Pivot prices | RAW high/low at size-offset |
| Break detection | RAW close prices |
| OB extreme selection | PARSED high/low (volatility-adjusted) |

## Running Validation

### Quick Start

```bash
# Prepare data directory
data/
  BTCUSD.P/
    1h.csv
  ETHUSD.P/
    1h.csv
  ...

# Run validation
python -c "
from quantedge.historical import run_historical_validation
from quantedge.market_data.models import Timeframe
from pathlib import Path

results = run_historical_validation(
    data_root=Path('data'),
    symbols=['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'],
    timeframe=Timeframe.H1,
    internal_length=5,
    swing_length=50,
    output_dir='validation_output'
)
"
```

### Output Structure

```
validation_output/
  BTCUSD.P/
    1h/
      events.jsonl      # Streaming event stream
      summary.json      # Summary statistics
  ETHUSD.P/
    1h/
      events.jsonl
      summary.json
  ...
```

### Summary JSON

```json
{
  "symbol": "BTCUSD.P",
  "timeframe": "1h",
  "dataset": {
    "dataset_id": "...",
    "candle_count": 8760,
    "start_time": "2024-01-01T00:00:00",
    "end_time": "2024-12-31T23:00:00",
    "file_hash": "...",
    "gaps": [],
    "quality_report": {...}
  },
  "config": {
    "internal_length": 5,
    "swing_length": 50,
    "atr_period": 200,
    "atr_multiplier": 2.0
  },
  "statistics": {
    "internal_leg_changes": 1250,
    "swing_leg_changes": 89,
    "internal_pivots": 625,
    "swing_pivots": 45,
    "internal_bos": 180,
    "internal_choch": 45,
    "swing_bos": 12,
    "swing_choch": 8,
    "internal_obs": 234,
    "swing_obs": 56,
    "ob_invalidations": 89,
    "ob_touches": 156,
    "total_candles": 8760,
    "total_events": 1245
  },
  "output": {
    "events_file": "validation_output/BTCUSD.P/1h/events.jsonl",
    "summary_file": "validation_output/BTCUSD.P/1h/summary.json",
    "total_events": 1245
  }
}
```

## Determinism

Running the same dataset twice produces identical event streams:

```python
# Run 1
engine1 = HistoricalReplayEngine(provider, config1)
result1 = engine1.run()

# Run 2
engine2 = HistoricalReplayEngine(provider, config2)
result2 = engine2.run()

# Events match exactly (except event_id timestamps)
assert len(result1.events) == len(result2.events)
for e1, e2 in zip(result1.events, result2.events):
    assert e1["event_type"] == e2["event_type"]
    assert e1["symbol"] == e2["symbol"]
    assert e1["candle_index"] == e2["candle_index"]
```

## Future Data Invariance

Adding future candles doesn't change events before a cutoff:

```python
# Run with base data (cutoff at Jan 15)
engine1.run(dataset_end="2024-01-15")

# Add future candles to CSV
append_future_candles()

# Run with extended data but same cutoff
engine2.run(dataset_end="2024-01-15")

# Events before cutoff are identical
for e1, e2 in zip(events1[:cutoff], events2[:cutoff]):
    assert e1["event_type"] == e2["event_type"]
    assert e1["candle_index"] == e2["candle_index"]
```

## Data Quality Validation

Before replay, datasets are validated:

- Duplicate timestamps
- Missing OHLC values
- Invalid OHLC relationships (high < low, etc.)
- Non-positive prices
- Timestamp ordering
- Timeframe consistency
- Gap detection

Corrupted input is rejected rather than silently repaired.

## Markets and Timeframes

### Primary Validation Markets

- BTCUSD.P
- ETHUSD.P
- SOLUSD.P
- XRPUSD.P

### Timeframe

- Primary: 1H (1 hour)
- Not currently: 15M, 5M, 4H, 1D

### Data Range

Recommended: 2024-01-01 through 2024-12-31 (full year)

If complete data unavailable, use largest continuous common window and report exact range.

## Testing

### Test Suite

```bash
cd engine
python -m pytest tests/ -v
```

### Key Test Categories

| Category | Tests | Purpose |
|----------|-------|---------|
| Causality | 3 | No look-ahead bias, causal ordering |
| Determinism | 2 | Identical output on repeat runs |
| Future Invariance | 2 | Future data doesn't change past |
| Raw vs Parsed | 2 | Separation maintained |
| Event Ordering | 2 | Events in candle order |
| Determinism | 1 | Byte-for-byte identical |
| Causal Generation | 3 | Pivot before break, break before OB |

### Run All Tests

```bash
cd engine
python -m pytest tests/ -v
```

## Known Limitations

1. **No TradingView pixel-perfect equivalence claimed** - Python implementation passes internal tests but hasn't been independently compared against TradingView/LuxAlgo pixel-perfect.

2. **Limited historical data** - Validation uses synthetic or limited real data. Full year data for all 4 symbols needed.

3. **OB lifecycle replay simplified** - Full OB lifecycle (FRESH → TOUCHED → USED/INVALIDATED) replay is partially implemented.

4. **Swing structure validation limited** - Swing (length=50) needs more extensive historical validation.

5. **No multi-timeframe validation** - Only 1H tested.

## Configuration Reference

```python
ReplayConfig(
    symbol="BTCUSD.P",           # Trading symbol
    timeframe=Timeframe.H1,      # Timeframe (1H primary)
    internal_length=5,           # Internal structure length
    swing_length=50,             # Swing structure length
    atr_period=200,              # ATR period for volatility parsing
    atr_multiplier=2.0,          # ATR multiplier
    output_dir="validation_output",  # Output directory
    dataset_start=None,          # Optional start filter
    dataset_end=None             # Optional end filter
)
```

## Output Files

### events.jsonl

Streaming JSONL with one event per line. Each event is a complete JSON object.

### summary.json

Machine-readable summary with statistics, configuration, and dataset metadata.

## CI/CD Integration

```yaml
# Example GitHub Actions step
- name: Run Historical Validation
  run: |
    cd engine
    python -m pytest tests/ -v
    python -c "
    from quantedge.historical import run_historical_validation
    from quantedge.market_data.models import Timeframe
    from pathlib import Path
    
    results = run_historical_validation(
        data_root=Path('data'),
        symbols=['BTCUSD.P', 'ETHUSD.P'],
        timeframe=Timeframe.H1,
        output_dir='validation_output'
    )
    for symbol, result in results.items():
        print(f'{symbol}: {result.internal_summary}, {result.swing_summary}, {result.ob_summary}')
    "
```

## Comparison with TradingView/LuxAlgo

To enable comparison:

1. Export events from this engine
2. Export equivalent events from TradingView (Pine Script)
3. Compare:
   - Pivot timestamps and prices
   - BOS/CHOCH timestamps, prices, types
   - OB formation indices and price levels
   - Event ordering

This document will be updated as comparison results become available.