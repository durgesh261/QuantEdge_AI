# QuantEdge AI

A comprehensive AI-powered trading platform with PAPER and LIVE trading modes.

## Overview

QuantEdge AI is a full-featured trading terminal that supports both paper trading (simulation) and live trading (real exchange execution). The application features a comprehensive set of safety gates, order block lifecycle management, and autonomous algorithmic execution capabilities.

## Key Features

- **PAPER Mode**: Simulation mode with zero real-order risk
- **LIVE Mode**: Autonomous algorithmic execution after explicit authorization
- **Order Block Lifecycle**: ACTIVE → TOUCHED → CONSUMED/INVALIDATED
- **Duplicate Protection**: Prevents duplicate orders from browser refresh, reconnect, multiple tabs/devices, and backend restart
- **Emergency Kill Switch**: Instantly halts all LIVE execution
- **Comprehensive Safety Gates**: 8-gate LiveTradingGuard validation
- **Multi-Device Synchronization**: Backend-authoritative state synchronization
- **Backup/Restore**: SQLite-based persistence with IncidentHistory tracking
- **Notifications**: Real-time alert system

## Architecture

### Execution Flow

```
Frontend LIVE control
  → CONFIRM_LIVE_TRADING exact phrase
  → ProductionController
    → LiveTradingGuard (8-gate validation)
      → ALLOW_LIVE_TRADING gate
      → Delta API keys gate
      → Kill switch gate
      → Delta connectivity gate
      → TradingView health gate
      → liveModeActive gate
      → explicitUserConfirmed gate
  → ExecutionEngineService
    → 12-rule validateOrder()
      → Exchange connectivity
      → Symbol whitelist
      → Product metadata
      → Quantity normalization
      → Risk validation (35% policy, leverage bounds)
      → Margin solvency
      → Maximum position notional
      → Maximum simultaneous trades
      → Idempotency/client order ID
      → Market-data freshness
  → DeltaAdapter
    → DeltaRestClient.placeOrder()
  → Exchange order
  → Order/position reconciliation
```

### Safety Gates (all must pass for LIVE authorization)

1. **exact `CONFIRM_LIVE_TRADING`** confirmation phrase
2. **`ALLOW_LIVE_TRADING`** environment variable = `true`
3. **Production API key** present
4. **Production API secret** present
5. **EmergencyKillSwitch** inactive
6. **Delta connection** healthy
7. **TradingView** connection healthy
8. **liveModeActive** authorized

### Important Directives

- **Deployment does NOT activate LIVE** - PAPER mode is the safe default
- **LIVE authorization requires explicit `CONFIRM_LIVE_TRADING` phrase**
- **No per-order manual confirmation needed** after LIVE is authorized
- **Every order must pass all 18+ validation rules**
- **Real orders = 0** until explicit controlled real-order test
- **Do NOT weaken or bypass safety controls**
- **Do NOT expose credentials** to frontend or logs
- **ALLOW_LIVE_TRADING remains disabled** until explicitly enabled

### Available Scripts

- `npm run build` - Build shared, backend, and frontend
- `npx vitest run` - Run unit tests (29/29 pass)
- `npx tsc --noEmit` - TypeScript type check

### State Management

- `executionMode`: `PAPER` | `LIVE` | `SHADOW`
- `isLiveModeActive`: boolean (frontend display, backend authoritative)
- `explicitUserConfirmed`: `true` only after `CONFIRM_LIVE_TRADING` authorization
- `ALLOW_LIVE_TRADING`: environment variable, disabled by default

### Safety Critical

- Never push real `.env` credentials to GitHub
- `.env.example` contains placeholders only
- Production `.env` stays on Azure/on-premises server
- All 29 core tests pass with PAPER mode
- Build and TypeScript check pass

## License

Proprietary - Internal Use Only