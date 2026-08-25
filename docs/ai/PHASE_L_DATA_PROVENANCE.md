# QuantEdge AI — Phase L Data Provenance & Integrity Audit

**Generated At (UTC)**: 2026-08-25T08:54:41.717548+00:00  
**Exchange**: Delta Exchange India (`https://api.india.delta.exchange/v2/history/candles`)  
**Timeframe**: 1-Hour (1H) Perpetual Futures  

## 1. Instrument Summary & Cryptographic Hashes

| Instrument | Candle Count | Date Range | SHA-256 Checksum | Validation Status |
|---|---:|:---:|:---:|:---:|
| **BTCUSD** | 19,479 | 2024-06-01 -> 2026-08-21 | `5e7bbab57e308b97e80980286690229fbc56db3263d19039303f32777c1e0ee9` | ✅ VALIDATED_CLEAN |
| **ETHUSD** | 19,479 | 2024-06-01 -> 2026-08-21 | `0ca80cbe3b83870f68ecc7cdd2ee00c4eb38b3f5145eb051ad5d3c187d30cb0f` | ✅ VALIDATED_CLEAN |
| **SOLUSD** | 19,479 | 2024-06-01 -> 2026-08-21 | `baf801dff6d7947e082bd3c15a2c65cc93487c2c741b686b004793949bd668e5` | ✅ VALIDATED_CLEAN |
| **XRPUSD** | 19,479 | 2024-06-01 -> 2026-08-21 | `7871b2966b7a4e680f1e4b0833f67423de0a94a2d3e7382c7bc309789261238f` | ✅ VALIDATED_CLEAN |

## 2. Market Data Invariants
- **Zero Synthetic Candles**: 100% genuine candles directly from Delta Exchange India.
- **Zero Interpolation**: Zero missing timestamp fills or artificial smoothing.
- **Zero Lookahead**: Causal state evaluation strictly at $T \le \text{decision\_bar}$.