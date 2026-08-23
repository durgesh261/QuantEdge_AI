# QuantEdge AI — Phase 9 API Gap Analysis
## Comprehensive Backend API Inventory, Feature Mapping & Gap Classification

---

## 1. Classification Methodology

Every planned frontend feature across both the **User App** and the **Developer App** is mapped against the authoritative Spring Boot backend and classified under one of the following statuses:

1. **`EXISTING API`**: Endpoint is fully implemented, authenticated, tested, and ready for frontend consumption.
2. **`PARTIALLY SUPPORTED`**: Endpoint exists but requires minor query parameter enhancements (e.g. additional filters or pagination fields).
3. **`BACKEND API GAP`**: Feature requires a new REST endpoint; data exists in PostgreSQL or memory.
4. **`DATA EXISTS BUT API IS MISSING`**: Table/Entity exists in schema (e.g., `audit_logs`, `strategy_setups`), but no dedicated CRUD/list controller is exposed.
5. **`DATA DOES NOT EXIST`**: Feature requires new database schema and entity design.
6. **`SECURITY/RBAC GAP`**: Endpoint exists but requires stricter role checks (e.g., `@PreAuthorize("hasRole('ADMIN')")`).
7. **`FRONTEND-ONLY FEATURE`**: Client-side calculation or UI state management requiring no new backend changes.

---

## 2. Comprehensive API Mapping Table

| Method | Endpoint Path | Auth Required | Minimum Role | Frontend Consumer | Classification | Description / Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/auth/signup` | Public | None | User App | `EXISTING API` | User registration with BCrypt hashing |
| `POST` | `/api/v1/auth/login` | Public | None | User App | `EXISTING API` | Login returning HttpOnly JWT cookies |
| `POST` | `/api/v1/auth/logout` | Public | None | User / Dev App | `EXISTING API` | Clears HttpOnly session cookies |
| `POST` | `/api/v1/auth/refresh` | Cookie | None | User / Dev App | `EXISTING API` | Refreshes JWT access token |
| `GET` | `/api/v1/auth/me` | JWT | `ROLE_USER` | User / Dev App | `EXISTING API` | Returns current user profile |
| `POST` | `/api/v1/account/connect` | JWT | `ROLE_USER` | User App | `EXISTING API` | Connects Delta India API keys (AES-256 encrypted) |
| `POST` | `/api/v1/account/verify` | JWT | `ROLE_USER` | User App | `EXISTING API` | Tests Delta connectivity & updates balance |
| `GET` | `/api/v1/account/status` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns exchange connection state |
| `GET` | `/api/v1/account/summary` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns equity, balance, and margin metrics |
| `POST` | `/api/v1/account/disconnect` | JWT | `ROLE_USER` | User App | `EXISTING API` | Disconnects exchange API connection |
| `GET` | `/api/v1/account/algo-config` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns risk and strategy configuration |
| `PUT` | `/api/v1/account/algo-config` | JWT | `ROLE_USER` | User App | `EXISTING API` | Updates risk limits and leverage |
| `GET` | `/api/v1/market/products` | Public | None | User App | `EXISTING API` | Returns tradable Delta India products |
| `GET` | `/api/v1/market/ticker/{symbol}`| Public | None | User App | `EXISTING API` | Returns 24h ticker, volume, mark price |
| `GET` | `/api/v1/market/candles` | Public | None | User App | `EXISTING API` | Returns OHLCV candles for TradingView chart |
| `GET` | `/api/v1/market/status` | Public | None | User / Dev App | `EXISTING API` | Returns Delta market data stream health |
| `GET` | `/api/v1/news` | Public | None | User App | `EXISTING API` | Returns 7-day categorized financial news |
| `GET` | `/api/v1/news/{id}` | Public | None | User App | `EXISTING API` | Returns single news article details |
| `GET` | `/api/v1/news/status` | Public | None | User / Dev App | `EXISTING API` | Returns CryptoCompare news provider telemetry |
| `POST` | `/api/v1/news/refresh` | JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Triggers on-demand news ingestion sync |
| `GET` | `/api/v1/economic-events` | Public | None | User App | `EXISTING API` | Returns 15-day rolling macroeconomic events |
| `GET` | `/api/v1/economic-events/{id}`| Public | None | User App | `EXISTING API` | Returns single economic release details |
| `GET` | `/api/v1/economic-events/status`| Public | None | User / Dev App | `EXISTING API` | Returns ForexFactory calendar provider telemetry |
| `POST` | `/api/v1/economic-events/sync` | JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Triggers on-demand economic calendar sync |
| `GET` | `/api/v1/notifications` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns in-app alert notifications |
| `POST` | `/api/v1/notifications/{id}/read`| JWT | `ROLE_USER` | User App | `EXISTING API` | Marks notification as read |
| `POST` | `/api/v1/notifications/read-all`| JWT | `ROLE_USER` | User App | `EXISTING API` | Marks all notifications as read |
| `GET` | `/api/v1/trade/status` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns trading system & algo status |
| `GET` | `/api/v1/trade/orders` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns user's orders with symbol/status filters |
| `GET` | `/api/v1/trade/positions` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns user's active/closed positions |
| `GET` | `/api/v1/trade/fills` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns trade execution fill records |
| `GET` | `/api/v1/trade/history` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns realized P&L trade history |
| `GET` | `/api/v1/trade/signals` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns qualified SMC strategy setups |
| `GET` | `/api/v1/ai/enrichments` | JWT | `ROLE_USER` | User App | `EXISTING API` | Returns AI composite confidence scores |
| `POST` | `/api/v1/trade/kill-switch` | JWT | `ROLE_USER` | User App | `EXISTING API` | Triggers emergency kill-switch |
| `POST` | `/api/v1/trade/kill-switch/reset`| JWT | `ROLE_USER` | User App | `EXISTING API` | Resets emergency kill-switch |
| `POST` | `/api/v1/trade/algo/toggle` | JWT | `ROLE_USER` | User App | `EXISTING API` | Enables/disables algorithmic trading loop |
| `GET` | `/api/v1/developer/status` | JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Returns JVM & system health telemetry |
| `GET` | `/api/v1/developer/diagnostics`| JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Returns roundtrip latency benchmarks |
| `GET` | `/api/v1/developer/logs` | JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Returns sanitized real-time logs |
| `GET` | `/api/v1/developer/sandbox/info`| JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Returns strategy sandbox metadata |
| `POST` | `/api/v1/developer/sandbox/simulate-tick`| JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Dry-run simulated market tick |
| `GET` | `/api/v1/developer/system/accounts`| JWT | `ROLE_DEVELOPER` | Dev App | `EXISTING API` | Returns multi-tenant account health summary |
| `POST` | `/api/v1/developer/locks/{id}/release`| JWT | `ROLE_ADMIN` | Dev App | `BACKEND API GAP` | Emergency manual lock release (Phase 9) |
| `GET` | `/api/v1/developer/db/migrations`| JWT | `ROLE_DEVELOPER` | Dev App | `BACKEND API GAP` | Returns Flyway schema migration history (Phase 9) |

---

## 3. Summary of Gaps

1. **User App Gaps**: **0 BACKEND GAPS**. 100% of the planned User App features (Auth, Dashboard, Trading Terminal, Lightweight Charts, SMC Overlays, Signals, News, Economic Calendar, Notifications, Orders, Positions, Fills, Risk Controls, Settings) are backed by existing, verified REST APIs.
2. **Developer App Gaps**: **2 MINOR OPTIONAL GAPS** (`POST /locks/{id}/release` and `GET /db/migrations`). All core telemetry, logs, sandbox, account health, and provider status endpoints are already fully functional.
