# Phase 5.7 — Persistent Versioned Algo Configuration, Dynamic Strategy Contract & Immutable Trade Snapshots

**Verdict: PERSISTENT_ALGO_CONFIGURATION_READY**

---

## 1. Executive Summary

Phase 5.7 establishes two core architectural pillars for QuantEdge AI:
1. **Persistent, Versioned Algorithmic Trading Configuration & Immutable Trade Snapshots**.
2. **Backend/Engine-Driven Dynamic Strategy Architecture**: Trading logic, signal generation, Order Block/FVG confirmation, risk calculations, and TP/SL decisions reside exclusively in the backend/Python engine, while the frontend operates strictly as a presentation, visualization, and configuration layer.

---

## 2. Fundamental Architectural Principle: Backend/Engine-Driven Logic

> **"Trading logic lives strictly in the backend and Python engine. The frontend is a configurable UI and visualization layer."**

```
                ┌─────────────────────────────────┐
                │        React Frontend           │
                │ Presentation / Configuration    │
                │ Visualization (NO Trade Logic)  │
                └────────────────┬────────────────┘
                                 │ Stable DTO API Contract
                ┌────────────────▼────────────────┐
                │       Java Spring Backend       │
                │ Auth / Config / DB Persistence  │
                │ Execution Safety & Isolation    │
                └────────────────┬────────────────┘
                                 │ Authoritative Commands
                ┌────────────────▼────────────────┐
                │         Python Engine           │
                │ SMC Strategy / FVG / Signals    │
                │ Risk Calculations / Validation  │
                └────────────────┬────────────────┘
                                 │ REST & Private WS
                ┌────────────────▼────────────────┐
                │   Delta Exchange India (Live)   │
                └─────────────────────────────────┘
```

### Strict Separation of Concerns:
- **The Frontend MUST NOT contain authoritative implementations of**:
  - SMC strategy rules or Market Structure analysis
  - Order Block, Liquidity, or Fair Value Gap (FVG) detection
  - Entry confirmations or signal scoring
  - Risk calculations, leverage formulas, or position sizing
  - Authoritative Take Profit / Stop Loss prices
  - Order validation gateways or execution state machines
  - Position management decisions
- **Future Strategy Modifications (e.g. Adding FVG Confirmation)**:
  - Requires updating solely the Python engine / backend strategy pipeline.
  - When the updated engine is deployed, the frontend automatically receives the new behavior via the stable DTO contract without requiring code rewrites in React.

---

## 3. Dynamic Strategy Contract (DTO Model)

The Python engine and backend output a strictly typed contract (`StrategyResult` / `StrategyDecision`):

```json
{
  "signal": "LONG",
  "symbol": "BTCUSD",
  "timeframe": "1h",
  "direction": "LONG",
  "entry": 95000.00,
  "stopLoss": 94000.00,
  "takeProfit": 98000.00,
  "riskReward": 3.0,
  "confidence": 88.5,
  "strategyName": "SMC",
  "strategyVersion": "2.1",
  "setupId": "SETUP_20260822_001",
  "status": "TRADE_SETUP_READY",
  "metadata": {
    "ob_confirmed": true,
    "fvg_confirmed": true,
    "liquidity_swept": true
  },
  "timestamp": "2026-08-22T07:25:00Z"
}
```

---

## 4. Multi-Tier Database & Configuration Separation

| Tier | Component | Responsibility | Mutability |
|---|---|---|---|
| **Tier 1** | **Strategy Implementation** | SMC rules, OB algorithms, FVG filters, trend analysis | Engine-driven, version-controlled |
| **Tier 2** | **Global Strategy Configuration** | System limits, tick sizes, maximum leverage bounds | Server-side / Database registry |
| **Tier 3** | **User-Specific Configuration** | `take_profit_pct`, `stop_loss_pct`, `risk_per_trade_pct`, `daily_loss_limit` | Account-isolated, user-editable |
| **Tier 4** | **Trade Configuration Snapshot** | `AlgoConfigurationSnapshot` pinned at signal creation | **Strictly Immutable** |
| **Tier 5** | **Historical Trade Result** | `TradeLifecycleRecord`, filled prices, realized PnL, close reasons | **Strictly Immutable** |

---

## 5. Configuration & Strategy Versioning Interaction

```
[User Config: Version 1 (TP=2%, SL=1%)]  &  [Engine Strategy: v2.1]
                     │
                     ▼
             AlgoConfiguration (v1)
                     │
     ┌───────────────┴───────────────┐
     ▼                               ▼
[Trade A (10:01)]             [Trade B (10:02)]
  Binds Config v1               Binds Config v1
  Binds Strategy v2.1           Binds Strategy v2.1
  TP=2.0%, SL=1.0%              TP=2.0%, SL=1.0%
                                     │
                     ┌───────────────┘
                     ▼
    [User Updates Config: Version 2 (TP=3%, SL=1.5%)]
    [Engine Strategy Deployed: v2.2 (FVG Added)]
                     │
                     ▼
             AlgoConfiguration (v2)
                     │
                     ▼
          [Trade C (10:05)]
            Binds Config v2
            Binds Strategy v2.2 (SMC_FVG)
            TP=3.0%, SL=1.5%

★ INVARIANT: Trade A & B permanently retain Config v1 and Strategy v2.1.
★ INVARIANT: Modifying configuration or deploying strategy v2.2 affects ONLY future trades.
```

---

## 6. Account Isolation & Security Model

| Action | API Endpoint | Security & Ownership Validation | Fail-Safe Default |
|---|---|---|---|
| **Get Algo Config** | `GET /api/v1/account/algo-config` | Validates authenticated user owns account | 400 Bad Request if unowned |
| **Update Algo Config** | `PUT /api/v1/account/algo-config` | Validates ownership, validates bounds, increments version | 400 Bad Request on invalid ranges |
| **Execute Trade** | `POST /api/v1/trade/execute` | Validates server snapshot; rejects frontend parameter tampering | `ENTRY_REJECTED` (0 exchange calls) |

---

## 7. Verification & Test Metrics

| Test Suite | Scope | Passed / Total | Time |
|---|---|---|---|
| **`test_phase5_7_algo_configuration.py`** | Defaults, Isolation, Versioning, Snapshots, Geometry, Immutability, Strategy Contract | **20 / 20** | 1.73s |
| **`test_phase5_7_signal_execution.py`** | Signal Bridge, Bracket Protection, Partial Fill Scaling, Kill Switch | **25 / 25** | 1.49s |
| **`test_phase5_6_private_websocket.py`** | Private WebSocket Stream, HMAC Auth, Orders/Positions/Trades Sync | **23 / 23** | 0.85s |
| **`test_phase5_5_account_connection.py`** | Delta India Auth, AES-256 Encryption, Read-Only Sync | **11 / 11** | 0.32s |
| **`test_phase5_4_order_execution.py`** | Order Execution, Idempotency, Gateway Validation, Rate Limits | **22 / 22** | 0.45s |
| **Full Engine Pytest Suite** | Complete SMC, Strategy, Execution, WebSocket, Persistence | **722 Passed, 1 Skipped, 0 Failed** | 19.55s |
| **Frontend Production Build** | `tsc && vite build` bundle compilation | **Clean Build (1602 modules)** | 5.82s |
| **Frozen SMC Core Verification** | Zero diff against baseline `b8095dc` | **ZERO DIFF** | - |
| **Credential Leakage Audit** | Repository secret scan | **0 Leaks** | - |
| **Real Orders Placed** | Development order placement | **ZERO (0)** | - |

---

## 8. Deployment Principles for Future Strategy Iterations

1. Developer enhances strategy logic (e.g. FVG confirmation or trailing stops) in the Python engine / Java backend.
2. Run backend & engine test suites (`pytest`).
3. Build and deploy backend/engine update.
4. React frontend consumes the stable DTO contract without needing changes to trading algorithms.
5. All pre-existing trades retain their original strategy and configuration version snapshots.
