# QuantEdge Python Engine

Market intelligence and strategy research engine for QuantEdge AI V2.

## Structure

```
engine/
├── pyproject.toml           # Project configuration
├── src/quantedge/           # Main package
│   ├── __init__.py
│   ├── __main__.py          # Entry point
│   ├── config.py            # Configuration management
│   ├── market_data/         # Market data models & providers
│   ├── smc/                 # Smart Money Concepts implementation
│   │   ├── models.py        # SMC data structures
│   │   ├── volatility.py    # ATR-based volatility parsing
│   │   ├── structure.py     # Swing/Internal structure detection
│   │   ├── order_blocks.py  # LuxAlgo OB detection
│   │   ├── liquidity.py     # Liquidity levels
│   │   ├── equal_levels.py  # Equal highs/lows
│   │   ├── fvg.py           # Fair Value Gaps
│   │   └── analyzer.py      # Main SMC analyzer
│   ├── strategy/            # Trading strategy
│   │   ├── models.py        # Strategy models
│   │   ├── confidence.py    # 9-factor confidence scoring
│   │   ├── risk.py          # Risk calculations
│   │   └── engine.py        # Strategy engine
│   ├── backtesting/         # Backtesting engine
│   └── research/            # AI/ML research (future)
├── tests/                   # Test suite
│   ├── conftest.py
│   ├── test_volatility.py
│   ├── test_structure.py
│   ├── test_order_blocks.py
│   └── test_strategy.py
└── README.md
```

## Installation

```bash
# Using uv (recommended)
uv pip install -e ".[dev]"

# Or using pip
pip install -e ".[dev]"
```

## Running Tests

```bash
# All tests
uv run pytest

# Specific test file
uv run pytest tests/test_volatility.py -v

# With coverage
uv run pytest --cov=quantedge --cov-report=html
```

## Key Concepts

### LuxAlgo SMC Implementation

This engine implements the **exact** LuxAlgo Smart Money Concepts logic:

1. **Volatility Parsing**: ATR(200) with 2x multiplier, inverts high/low for high volatility
2. **Dual Structure**: Internal (len=5) and Swing (len=50) - never merged
3. **BOS/CHOCH**: Properly classified based on prior trend
4. **Order Blocks**: Selected from parsed extremes (min low for bullish, max high for bearish)
5. **OB Lifecycle**: touchCount, isUsed, invalidation by close

### Strategy (9-Factor Confidence)

| Factor | Weight |
|--------|--------|
| Trend Alignment | 15 |
| OB Freshness | 15 |
| First Touch | 15 |
| BOS/CHOCH | 15 |
| Liquidity Sweep | 10 |
| Premium/Discount | 10 |
| Session/Volatility | 5 |
| Risk/Reward | 10 |
| News/Macro | 5 |
| **Total** | **100** |

Threshold: **85**

### Risk Model

- Risk: 35% of account balance per trade
- Target: 60% of account balance
- Max Leverage: 100x
- Account R:R: ~1.71
- SL: OB boundary (no ATR offset)
- TP: Calculated for 60% account growth

## Development

### Code Quality

```bash
# Format
uv run black src tests
uv run isort src tests

# Lint
uv run ruff check src tests
uv run mypy src
```

### Adding New Tests

1. Create test file in `tests/test_<module>.py`
2. Use fixtures from `conftest.py`
3. Follow naming: `test_<functionality>_<scenario>`
4. Run with `uv run pytest tests/test_<module>.py -v`

## Configuration

Environment variables (see `.env.example`):

```bash
ENVIRONMENT=development
LOG_LEVEL=DEBUG
DATABASE_URL=postgresql://...
DELTA_API_KEY=...
DELTA_API_SECRET=...
```

## Architecture Notes

- **No look-ahead bias**: All calculations use only data available at candle time
- **Deterministic**: Same input -> same output (given same config)
- **Testable**: Every component has isolated unit tests
- **Modular**: Each SMC concept in separate module
- **Extensible**: Easy to add new indicators/strategies