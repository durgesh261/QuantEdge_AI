# Phase 5.0 — Live Execution Architecture & Delta Exchange India Audit

**Verdict: LIVE_EXECUTION_ARCHITECTURE_READY**

---

## 1. Repository Baseline Verification

| Item | Value |
| :--- | :--- |
| HEAD Commit | `82e812f` — chore(repo): clean repository for real trading Phase 5 |
| Working Tree | Clean (git status --short: empty) |
| Full Test Suite | 536 passed, 1 skipped, 0 failed |
| Frozen SMC Diff | **ZERO DIFF** vs baseline `b8095dc` |

---

## 2. Existing Architecture Map

```
╔══════════════════════════════════════════════════════════════════╗
║                     CURRENT PRODUCTION STACK                    ║
╠══════════════════════════════════════════════════════════════════╣
║  LAYER            │  TECHNOLOGY          │  STATUS              ║
╠═══════════════════╪══════════════════════╪══════════════════════╣
║  Frontend          │  React + TypeScript  │  Auth + UI scaffold  ║
║  Backend Auth      │  Spring Boot / JWT   │  COMPLETE           ║
║  Database Schema   │  PostgreSQL          │  COMPLETE (Flyway)   ║
║  Market Data WS    │  Python asyncio      │  COMPLETE           ║
║  Candle Ingestion  │  Python CSV/REST     │  COMPLETE           ║
║  SMC Engine        │  Python Frozen Core  │  COMPLETE (FROZEN)   ║
║  Incremental SMC   │  Python             │  COMPLETE           ║
║  Strategy Layer    │  Python             │  COMPLETE (Ph 4.0)   ║
║  Signal Qual.      │  Python             │  COMPLETE (Ph 4.1)   ║
║  Risk/Reward       │  Python             │  COMPLETE (Ph 4.2)   ║
╠═══════════════════╪══════════════════════╪══════════════════════╣
║  Delta Exec Client │  —                   │  MISSING (Phase 5.1) ║
║  Account/Balance   │  —                   │  MISSING (Phase 5.2) ║
║  Order Validation  │  —                   │  MISSING (Phase 5.3) ║
║  Real Order Submit │  —                   │  MISSING (Phase 5.4) ║
║  Order/Fill WS     │  —                   │  MISSING (Phase 5.5) ║
║  Emergency Controls│  —                   │  MISSING (Phase 5.6) ║
║  Backend Exec API  │  —                   │  MISSING (Phase 5.7) ║
║  Live Trading UI   │  —                   │  MISSING (Phase 5.8) ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 3. Existing Delta Exchange Integrations

### 3.1 Public Market Data (COMPLETE)

| Component | File | Endpoint Used |
| :--- | :--- | :--- |
| REST candle history | `engine/src/quantedge/market_data/ingestion.py:L26` | `https://api.india.delta.exchange/v2/history/candles` |
| WebSocket public stream | `engine/src/quantedge/market_data/delta_websocket.py:L41` | `wss://socket.india.delta.exchange` |
| WS public subscription | `delta_websocket.py` | channel: `candlestick_1h`, symbol: `BTCUSD` |

These are **public (unauthenticated) endpoints** — no API key required.

### 3.2 Authenticated Private APIs (MISSING)

No code currently exists for:
- Account balance queries
- Order placement
- Order status
- Order cancellation
- Position queries
- Private WebSocket subscriptions (orders/fills/positions)

### 3.3 API Credential Storage (SCHEMA PRESENT, LOGIC MISSING)

The database table `delta_connections` exists with fields:
```sql
encrypted_api_key TEXT NOT NULL
encrypted_api_secret TEXT NOT NULL
environment VARCHAR(20) CHECK (environment IN ('TESTNET', 'LIVE'))
connection_status VARCHAR(20)
```

The `SECURITY.md` specifies AES-256-GCM encryption for secrets at rest, but no Java service implementing this encryption/decryption has been created yet.

---

## 4. Existing Backend Authentication

| Feature | Status | File |
| :--- | :--- | :--- |
| JWT cookie-based auth | **COMPLETE** | `AuthController.java`, `JwtTokenProvider.java` |
| User entity + repository | **COMPLETE** | `auth/entity/User.java`, `UserRepository.java` |
| BCrypt password hashing | **COMPLETE** | `UserService.java` |
| JWT filter chain | **COMPLETE** | `JwtAuthenticationFilter.java` |
| CORS configuration | **COMPLETE** | `WebConfig.java` |
| HTTP endpoints (`/api/v1/auth/*`) | **COMPLETE** | `AuthController.java` |

No trading, account, order, position, or risk management controllers exist yet in Java.

---

## 5. Existing Database Tables (from V1__initial_schema.sql)

All tables already created by Flyway migration. No new schema migration is needed for Phase 5 unless audit reveals gaps.

| Table | Columns | Status |
| :--- | :--- | :--- |
| `users` | id, email, password_hash, name, is_active | ✅ Complete |
| `user_settings` | timezone, theme, notifications | ✅ Complete |
| `trading_accounts` | account_type (LIVE), balance, currency | ✅ Complete |
| `delta_connections` | encrypted_api_key, encrypted_api_secret, environment (TESTNET/LIVE), connection_status | ✅ Complete |
| `risk_configurations` | risk_per_trade_pct, max_leverage, max_concurrent_trades, max_daily_loss_pct | ✅ Complete |
| `strategy_configurations` | confidence_threshold, timeframe, symbols, SMC params | ✅ Complete |
| `orders` | delta_order_id, client_order_id, symbol, side, order_type, status, price, quantity, leverage | ✅ Complete |
| `positions` | entry_price, quantity, leverage, unrealized_pnl, stop_loss_price, take_profit_price | ✅ Complete |
| `order_blocks` | SMC OB registry for audit/correlation | ✅ Complete |
| `journal_entries` | trade journal with R-multiple | ✅ Complete |
| `audit_logs` | action, resource_type, old/new_values, ip_address | ✅ Complete |

> [!IMPORTANT]
> The schema supports all Phase 5 data requirements. **No new Flyway migration is required for Phase 5.1–5.6** unless additional fields are identified during implementation. A Phase 5 migration (V2) will only be added if gaps are found.

### Potential Schema Gaps for Phase 5

| Gap | Required For | Resolution |
| :--- | :--- | :--- |
| `algo_enabled` flag on `trading_accounts` | Emergency kill switch | Add via V2 migration |
| `trading_enabled` global flag | Global emergency halt | Add via V2 migration |
| `fills` table (trade execution records) | Fill reconciliation | May extend `orders` or add V2 `fills` table |
| `execution_events` table | Full audit trail | Optional V2 addition |

---

## 6. Existing Frontend Trading Components

| File | Status | Notes |
| :--- | :--- | :--- |
| `frontend/src/features/trading/LiveTrading.tsx` | Placeholder ("Coming Soon") | Full implementation needed in Phase 5.8 |
| `frontend/src/services/api.ts` | Base axios instance with JWT cookie | Needs trading endpoints added |
| `frontend/src/stores/authStore.ts` | Auth state management | Adequate for Phase 5 |
| `frontend/src/App.tsx` | Routing — no paper trading | Clean, ready for Phase 5.8 |
| `frontend/src/components/Layout.tsx` | Navigation — Live Trading present | Ready |

---

## 7. Delta Exchange India API Endpoints Required

### 7.1 Authentication Mechanism

All private REST endpoints require three HTTP headers:

| Header | Value |
| :--- | :--- |
| `api-key` | User's Delta Exchange API key |
| `timestamp` | Current Unix epoch (seconds, integer) |
| `signature` | HMAC-SHA256 of the message string |

**Signature Construction:**
```
message = HTTP_METHOD + TIMESTAMP + URL_PATH + QUERY_STRING + REQUEST_BODY

# Where:
# - HTTP_METHOD: "GET", "POST", "DELETE" (uppercase)
# - TIMESTAMP: same integer as header
# - URL_PATH: e.g., "/v2/orders"
# - QUERY_STRING: URL-encoded query params (empty string if none)
# - REQUEST_BODY: JSON string of body (empty string if none)
```

**Python Signing Function (will be implemented in Phase 5.1):**
```python
import hmac, hashlib, time

def sign_request(api_secret: str, method: str, path: str,
                 query: str = "", body: str = "") -> tuple[str, str]:
    timestamp = str(int(time.time()))
    message = method + timestamp + path + query + body
    signature = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp
```

> [!IMPORTANT]
> Signature window is typically ±5 seconds. Clocks must be synchronized (NTP).

### 7.2 REST Private Endpoints (Phase 5.2 through 5.4)

**Base URL: `https://api.india.delta.exchange`**

| Phase | Purpose | Method | Path |
| :--- | :--- | :--- | :--- |
| 5.2 | Get wallet balances | `GET` | `/v2/wallet/balances` |
| 5.2 | Get open positions | `GET` | `/v2/positions/margined` |
| 5.3 | Get product info (tick size, precision) | `GET` | `/v2/products` |
| 5.4 | Create order | `POST` | `/v2/orders` |
| 5.4 | Cancel order | `DELETE` | `/v2/orders/{id}` |
| 5.4 | Get order by ID | `GET` | `/v2/orders/{id}` |
| 5.4 | Get open orders | `GET` | `/v2/orders?state=open` |
| 5.5 | Get order fills | `GET` | `/v2/fills` |

**Order Creation Payload:**
```json
{
  "product_id": 27,
  "product_symbol": "BTCUSD",
  "size": 1,
  "side": "buy",
  "order_type": "limit_order",
  "limit_price": "95000.00",
  "time_in_force": "gtc",
  "client_order_id": "qe-2026-08-21-uuid"
}
```

Order types supported:
- `market_order` — immediate execution at market price
- `limit_order` — executes at `limit_price` or better

> [!NOTE]
> `client_order_id` is the idempotency key — Delta Exchange will reject duplicate submissions with the same `client_order_id`. This is the primary duplicate-order protection mechanism.

### 7.3 WebSocket Private Channels (Phase 5.5)

**WS Endpoint: `wss://socket.india.delta.exchange`** (same endpoint as public market data)

**Authentication Message (sent after connection):**
```json
{
  "type": "key-auth",
  "payload": {
    "api-key": "YOUR_API_KEY",
    "signature": "HMAC_SHA256(GET + TIMESTAMP + /live)",
    "timestamp": "1724261234"
  }
}
```

**Private Channel Subscriptions:**
```json
{
  "type": "subscribe",
  "payload": {
    "channels": [
      {"name": "orders", "symbols": ["BTCUSD"]},
      {"name": "positions", "symbols": ["BTCUSD"]},
      {"name": "user_trades", "symbols": ["BTCUSD"]}
    ]
  }
}
```

| Channel | Events | Use |
| :--- | :--- | :--- |
| `orders` | Order state changes (open → filled/cancelled/rejected) | Real-time order lifecycle |
| `positions` | Position P&L, quantity updates | Real-time position sync |
| `user_trades` | Individual fill records | Fill reconciliation |

---

## 8. Security Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   SECURITY BOUNDARY                      │
│                                                          │
│  Frontend (React)                                        │
│  ┌──────────────────────────────────────────────┐        │
│  │  JWT cookie (HttpOnly, SameSite=Lax)         │        │
│  │  NO Delta API key stored here                │        │
│  │  NO order parameters bypassing backend       │        │
│  └─────────────────┬────────────────────────────┘        │
│                    │ HTTPS only                           │
│  Backend (Spring Boot)                                   │
│  ┌──────────────────────────────────────────────┐        │
│  │  Validates JWT on every request              │        │
│  │  Owns Delta API key (AES-256-GCM encrypted)  │        │
│  │  Independent risk validation                 │        │
│  │  Signs Delta API requests server-side        │        │
│  │  Never returns API secret to frontend        │        │
│  └─────────────────┬────────────────────────────┘        │
│                    │ TLS                                  │
│  Delta Exchange India API                                │
│  ┌──────────────────────────────────────────────┐        │
│  │  HMAC-SHA256 signed requests                 │        │
│  │  api.india.delta.exchange                    │        │
│  └──────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────┘
```

**Key Security Rules:**
1. Delta API secrets are encrypted at rest (AES-256-GCM) in `delta_connections` table.
2. Python engine never receives Delta credentials — it operates on public candle data only.
3. Backend Spring Boot is the sole authority for authenticated Delta Exchange communication.
4. Frontend never makes direct calls to Delta Exchange.
5. JWT secret must be minimum 256 bits in production (already enforced in config).
6. Cookie flags: `HttpOnly`, `SameSite=Lax`, `Secure=true` in production.

---

## 9. Required Database Changes (V2 Migration)

A `V2__execution_controls.sql` migration will be needed in Phase 5.3/5.6:

```sql
-- Phase 5 execution control fields
ALTER TABLE trading_accounts
    ADD COLUMN algo_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN trading_enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- Global emergency halt (admin-only table)
CREATE TABLE trading_controls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    global_trading_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    halt_reason TEXT,
    halted_by UUID REFERENCES users(id),
    halted_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Execution events for full audit trail
CREATE TABLE execution_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID REFERENCES orders(id),
    event_type VARCHAR(50) NOT NULL,
    delta_response JSONB,
    raw_payload JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_execution_events_order ON execution_events(order_id);
CREATE INDEX idx_execution_events_type ON execution_events(event_type);
```

---

## 10. Required Backend Java Services

| Service | Purpose | Phase |
| :--- | :--- | :--- |
| `DeltaCredentialService` | Encrypt/decrypt API keys (AES-256-GCM), test connection | 5.1 |
| `DeltaRestClient` | HMAC-signed REST client for Delta India | 5.1 |
| `AccountBalanceService` | Fetch and cache wallet balances + positions | 5.2 |
| `OrderValidationService` | Pre-submission risk gate (15 checks) | 5.3 |
| `OrderExecutionService` | Submit orders with idempotency, track lifecycle | 5.4 |
| `FillReconciliationService` | WebSocket + REST fill reconciliation | 5.5 |
| `EmergencyControlService` | Kill switch, algo enable/disable | 5.6 |
| REST Controllers | `/accounts`, `/orders`, `/positions`, `/trading/status` | 5.7 |

---

## 11. Required Frontend Changes

| Component | Change | Phase |
| :--- | :--- | :--- |
| `LiveTrading.tsx` | Full real-trading UI | 5.8 |
| `services/api.ts` | Trading endpoints (orders, positions, balance) | 5.7 |
| Order confirmation modal | Explicit user confirmation before real order | 5.8 |
| Position monitor component | Live P&L, SL/TP display | 5.8 |
| Emergency stop button | Visible, prominent kill switch | 5.8 |

---

## 12. Order Lifecycle State Machine

```
SIGNAL (from Strategy Engine)
   │
   ▼
VALIDATED (pre-submission risk gate passes)
   │
   ▼
SUBMISSION_PENDING (saved to DB, not yet submitted)
   │
   ▼
SUBMITTED (HTTP 200 from Delta)
   │
   ├──────────────────────────────────┐
   ▼                                  ▼
PARTIALLY_FILLED              REJECTED (exchange declined)
   │                                  │
   ▼                                  ▼
FILLED (all quantity)           CANCELLED (by user or system)
   │
   ▼
[TERMINAL — immutable in DB]

Additionally:
EXPIRED (GTC order expired by exchange)
FAILED  (timeout / network — status unknown)
```

> [!CAUTION]
> A `FAILED` order means the exchange response was not received. **Never re-submit without first confirming via REST GET `/v2/orders/{client_order_id}` that the original was NOT accepted.**

---

## 13. Emergency Controls Architecture

```
EmergencyControlService
   │
   ├─ killSwitch()
   │    └─ SET trading_controls.global_trading_enabled = FALSE
   │    └─ OrderExecutionService rejects ALL new orders immediately
   │
   ├─ disableAccount(accountId)
   │    └─ SET trading_accounts.algo_enabled = FALSE
   │    └─ No new orders from this account
   │
   └─ cancelOpenOrders(accountId)
        └─ Calls DELETE /v2/orders for each open order
        └─ Records cancellation events in audit_logs
```

> [!WARNING]
> The kill switch **prevents new order creation only**. It does NOT automatically close existing positions. Explicit position closing is a separate deliberate action — never performed automatically.

---

## 14. Failure / Reconciliation Risks

| Risk | Mitigation |
| :--- | :--- |
| REST timeout after order submission | Mark as `FAILED`, then query by `client_order_id` before retry |
| Duplicate `client_order_id` | Delta rejects; backend detects and does not re-submit |
| WebSocket disconnect | REST poll fallback every 30s for open orders |
| Database crash after submit, before WS confirm | Reconcile on startup via GET `/v2/orders?state=open` |
| Clock skew | NTP sync; reject if timestamp outside ±5s window |
| Exchange maintenance | Trading disabled until connection health check passes |
| Stale position data | Position re-sync from REST on WebSocket reconnect |

---

## 15. Testnet / Production Configuration Audit

| Location | URL | Type | Status |
| :--- | :--- | :--- | :--- |
| `application.yml:L75` | `https://api.delta.exchange` | Global exchange (not India) | **WRONG** — Should be `api.india.delta.exchange` |
| `application.yml:L76` | `https://api-testnet.delta.exchange` | Global testnet | Retained for dev safety |
| `docker-compose.yml:L47` | `https://api.delta.exchange` | docker env | **WRONG** — Should be `api.india.delta.exchange` |
| `ingestion.py:L26` | `https://api.india.delta.exchange/v2/history/candles` | India candle API | ✅ Correct |
| `delta_websocket.py:L41` | `wss://socket.india.delta.exchange` | India WebSocket | ✅ Correct |

> [!WARNING]
> `application.yml` and `docker-compose.yml` reference `api.delta.exchange` (global) instead of `api.india.delta.exchange` (India). These will be corrected in Phase 5.1 when the authenticated Delta client is implemented.

---

## 16. Phase 5 Sub-Phase Implementation Plan

| Sub-Phase | Title | Scope | Tests |
| :--- | :--- | :--- | :--- |
| **5.1** | Secure Delta Exchange India Authenticated Client | HMAC signing, credential encryption/decryption service, connection test | Unit tests for signing, credential round-trip |
| **5.2** | Account / Balance / Position Synchronization | REST calls for wallet, positions; startup reconciliation | Mock Delta REST, balance assertions |
| **5.3** | Order Validation & Risk Gateway | 15-check pre-submission validation, Spring Boot authoritative gateway, `trading_controls` migration | All validation failure paths |
| **5.4** | Real Order Submission | `client_order_id` idempotency, POST `/v2/orders`, FAILED state handling, timeout recovery | All order states, duplicate protection |
| **5.5** | Order / Fill WebSocket Reconciliation | Private WS auth, orders/positions/user_trades channels, REST fallback | WS event parsing, reconnect, duplicate events |
| **5.6** | Emergency Controls & Failure Recovery | Kill switch, account disable, cancel-open-orders, startup reconciliation | Kill switch blocks orders, reconciliation paths |
| **5.7** | Backend Execution API | Spring Boot REST controllers: `/accounts`, `/orders`, `/positions`, `/trading/status` | Controller tests, authorization |
| **5.8** | Frontend Live Trading UI | Full `LiveTrading.tsx`, order confirmation modal, position monitor, emergency button | TypeScript compilation |
| **5.9** | End-to-End Readiness Audit | Full stack test, final security review, frozen SMC verification, final commit | All 536+ tests pass |

---

## 17. What Exists vs What is Missing — Summary

### EXISTS AND COMPLETE
- Spring Boot JWT auth (login, signup, refresh, logout)
- PostgreSQL schema for users, accounts, orders, positions, fills, audit logs
- Python SMC engine (frozen, validated)
- Python Strategy/Signal/Risk/Reward layer (Phases 4.0–4.2)
- Public Delta Exchange India candle ingestion and WebSocket streaming
- React frontend skeleton with auth, dashboard, journal, analytics, settings

### MISSING — REQUIRED FOR PHASE 5
- Java `DeltaRestClient` with HMAC-SHA256 signing (Phase 5.1)
- AES-256-GCM credential encryption service (Phase 5.1)
- Account balance / position sync from Delta (Phase 5.2)
- Pre-order risk validation gateway (Phase 5.3)
- Real order placement service with idempotency (Phase 5.4)
- Private WebSocket order/fill/position reconciliation (Phase 5.5)
- Emergency kill switch and controls (Phase 5.6)
- Spring Boot trading REST API controllers (Phase 5.7)
- Full `LiveTrading.tsx` real-trading UI (Phase 5.8)
- `V2__execution_controls.sql` migration (Phase 5.3)

---

## 18. Frozen SMC Core — Final Verification

```bash
$ git diff b8095dc -- engine/src/quantedge/smc/structure.py \
                     engine/src/quantedge/smc/order_blocks.py \
                     engine/src/quantedge/smc/volatility.py
# Output: EMPTY (ZERO DIFF)
```

- `engine/src/quantedge/smc/structure.py` — **ZERO DIFF**
- `engine/src/quantedge/smc/order_blocks.py` — **ZERO DIFF**
- `engine/src/quantedge/smc/volatility.py` — **ZERO DIFF**

No SMC core file was modified in Phase 5.0.

---

## 19. Full Regression Test Result (Post-Audit)

```text
======================= 536 passed, 1 skipped in 24.80s =======================
```
- **Passed**: 536
- **Skipped**: 1 (pre-existing TV sync skip)
- **Failed**: 0

---

## 20. Explicit Statement

> **Phase 5 order-placement code has NOT been written.** This document is exclusively the Phase 5.0 architecture audit. No real orders will be placed, no exchange API calls are made by application code, and no credentials are exposed.

---

## Final Verdict

# `LIVE_EXECUTION_ARCHITECTURE_READY`
