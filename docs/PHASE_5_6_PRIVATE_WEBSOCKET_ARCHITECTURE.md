# Phase 5.6 — Private Delta WebSocket & Real-Time Account State Architecture

**Verdict: PRIVATE_WEBSOCKET_SYNC_READY**

---

## 1. Executive Summary

Phase 5.6 establishes the production-grade **private authenticated WebSocket integration** with Delta Exchange India (`wss://socket.india.delta.exchange`). 

This provides real-time streaming updates for account balances, active positions, open orders, and trade fills without relying solely on REST polling, while enforcing a **dual-layer reconciliation model** where authoritative REST synchronization resolves any stream discrepancies.

### Core Guarantees:
1. **Authenticated Stream**: WebSocket connects to `wss://socket.india.delta.exchange` and authenticates via `key-auth` using HMAC-SHA256 signature generated server-side.
2. **Private Account Channels**: Subscribes to `orders`, `positions`, `user_trades`, and `margins`.
3. **Event Normalization & Validation**: All raw JSON frames are validated, sanitised, and converted to strongly typed immutable dataclasses (`DeltaOrderEvent`, `DeltaPositionEvent`, `DeltaFillEvent`, `DeltaMarginEvent`).
4. **Authoritative REST Precedence**: Periodic and post-reconnect REST reconciliation runs in parallel. If any discrepancy occurs between WebSocket events and REST snapshots, **REST state always wins**.
5. **Robust Reconnection**: Full connection state machine (`DISCONNECTED` → `CONNECTING` → `AUTHENTICATING` → `CONNECTED` → `RECONNECTING` → `STALE` → `ERROR`) with exponential backoff, jitter, heartbeat timeout detection, and automatic subscription recovery.
6. **Zero Credential Exposure**: API secrets are never transmitted over WebSocket URLs, never logged, never returned to frontend, and decrypted only in server memory.
7. **Strict Fail-Safe Invariants**: Zero real orders are placed, cancelled, or modified (`algo_enabled = false`, `kill_switch_active = true`).

---

## 2. Event Flow & Component Diagram

```
Delta Exchange India (wss://socket.india.delta.exchange)
        │
        ├─ 1. Handshake & Connect
        ├─ 2. key-auth { api-key, timestamp, signature = HMAC-SHA256("GET" + ts + "/live") }
        ├─ 3. subscribe { channels: ["orders", "positions", "user_trades", "margins"] }
        ▼
QuantEdge DeltaPrivateWebSocketClient
        │
        ├─ Incoming Raw JSON Event
        ▼
EventValidator & Normalizer
        ├─ Schema validation (types, Decimal precision, ranges)
        ├─ Quarantine malformed/unknown events (without crashing)
        ▼
Typed Events: DeltaOrderEvent | DeltaPositionEvent | DeltaFillEvent | DeltaMarginEvent
        │
        ▼
LocalStateStore / PostgreSQL State Manager
        ├─ Dual-Layer Idempotency (timestamp & sequence check)
        ├─ Deduplication (ignore older/duplicate events)
        ├─ Update Position, Order, TradingAccount entities
        │
        ▲ (Background Periodic & On-Reconnect Trigger)
LiveAccountSyncService (Authoritative REST Reconciliation)
        ├─ GET /v2/wallet/balances
        ├─ GET /v2/positions/margined
        └─ GET /v2/orders?state=open
        └─ [REST WINS ON CONFLICT]
        │
        ▼
Spring Boot Backend (AccountController / AccountManagementService)
        │ (REST Status & Summary APIs / SSE Stream)
        ▼
Frontend React Client (Zustand accountStore -> LiveTrading.tsx / Settings.tsx)
        ├─ REST Connection: CONNECTED (Green)
        ├─ Private WS: CONNECTED / HEALTHY (Green Pulse)
        ├─ Real-time Balances, Margined Positions, Open Orders
        └─ Read-Only Safety Banner (Algo Disabled / Kill Switch Active)
```

---

## 3. WebSocket Protocol Specification

### 3.1 Endpoint
- Production Endpoint: `wss://socket.india.delta.exchange`

### 3.2 Authentication (`key-auth`)
Sent immediately upon connection opening:
```json
{
  "type": "key-auth",
  "payload": {
    "api-key": "<DELTA_API_KEY>",
    "signature": "<HMAC_SHA256_HEX>",
    "timestamp": "<UNIX_TIMESTAMP_SECONDS_OR_MS>"
  }
}
```
**Signature Generation**:
$$\text{Signature} = \text{HMAC-SHA256}(\text{API\_SECRET}, \text{"GET"} + \text{timestamp} + \text{"/live"})$$

### 3.3 Channel Subscriptions
Sent upon receiving successful authentication acknowledgment:
```json
{
  "type": "subscribe",
  "payload": {
    "channels": [
      { "name": "orders", "symbols": ["all"] },
      { "name": "positions", "symbols": ["all"] },
      { "name": "user_trades", "symbols": ["all"] },
      { "name": "margins", "symbols": ["all"] }
    ]
  }
}
```

### 3.4 Heartbeat Monitoring
- Ping message sent every 30 seconds:
```json
{ "type": "ping" }
```
- Server responds with:
```json
{ "type": "pong" }
```
- If no pong or message is received for 60 seconds, stream is marked `STALE` and reconnect is triggered.

---

## 4. Normalization Models

| Event Type | Channel | QuantEdge Internal Model | Primary Fields |
| :--- | :--- | :--- | :--- |
| `orders` | `orders` | `DeltaOrderEvent` | `order_id`, `client_order_id`, `symbol`, `side`, `order_type`, `size`, `unfilled_size`, `price`, `status`, `timestamp` |
| `positions` | `positions` | `DeltaPositionEvent` | `symbol`, `side`, `size`, `entry_price`, `mark_price`, `liquidation_price`, `unrealized_pnl`, `margin`, `leverage` |
| `user_trades` | `user_trades` | `DeltaFillEvent` | `trade_id`, `order_id`, `symbol`, `side`, `size`, `price`, `fee`, `role`, `timestamp` |
| `margins` | `margins` | `DeltaMarginEvent` | `asset_symbol`, `balance`, `available_balance`, `position_margin`, `order_margin` |

All financial quantities and prices are parsed strictly into `Decimal` instances with zero floating-point arithmetic.

---

## 5. Dual-Layer Reconciliation Matrix

| Scenario | WebSocket Action | REST Reconciliation Action | Final Authoritative State |
| :--- | :--- | :--- | :--- |
| Normal order fill | Updates order to `FILLED`, position size updated | Matches state on next 30s cycle | `FILLED` |
| Missed WS event during network blip | State is temporarily stale | REST sync fetches `/v2/orders` and `/v2/positions` | REST snapshot overwrites |
| Duplicate WS event | Idempotency guard drops duplicate | No change needed | Single record retained |
| Out-of-order WS event (old after new) | Timestamp comparison drops older event | No change needed | Newer state preserved |
| WS Disconnect / Reconnect | Triggers reconnect backoff | Immediately triggers full `sync()` on reconnect | REST state restored |

---

## 6. Safety & Security Invariants

1. **Zero Real Orders**: No component in Phase 5.6 has authority or capability to call `POST /v2/orders` or send order submission frames over WebSocket.
2. **Fail-Safe Defaults**:
   - `algo_enabled = false`
   - `kill_switch_active = true`
3. **Decryption in Memory Only**: API secrets decrypted from AES-256-GCM only when generating HMAC-SHA256 signature, then immediately eligible for GC.
4. **Masked Keys**: Only masked keys (`DAlq***97uI`) exposed across all logs, APIs, and UIs.
