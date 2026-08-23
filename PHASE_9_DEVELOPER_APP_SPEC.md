# QuantEdge AI — Phase 9 Developer App Specification
## Dedicated System Observability, Diagnostic Telemetry & Sandbox Lab Console

---

## 1. Product Overview & Developer Persona

The **QuantEdge Developer App** (`developer-app/`) is an administrative and engineering workstation designed for quantitative analysts, systems engineers, and operations administrators with role `ROLE_DEVELOPER` or `ROLE_ADMIN`.

### Core Operational Objectives
- Inspect system telemetry, JVM runtime health, PostgreSQL connection pools, and Python engine status.
- Monitor multi-tenant trading account health, active trade locks, open orders, and error flags.
- Inspect 1H SMC strategy setups and live AI signal enrichment pipelines.
- Supervise external provider synchronization (Delta Exchange India, CryptoCompare News, Faireconomy Macroeconomic Calendar).
- Proactively diagnose API latency bottlenecks and review real-time sanitized log streams.
- Experiment with dry-run market price ticks in the isolated Strategy Sandbox Lab without risking real capital.

---

## 2. Complete Page-by-Page Inventory & UI/UX Blueprints

### Page 1: Developer Command Center (`/developer`)
- **Purpose**: Executive operational telemetry dashboard displaying platform vitals and service status.
- **Widgets**:
  1. **Core Service Vitals**:
     - Spring Boot Backend: Version `2.0.0-SNAPSHOT`, Uptime, JVM Heap (Used / Total / Max), Active Threads.
     - Python SMC Engine: Bridge status (`CONNECTED` / `OFFLINE`), Heartbeat latency.
     - PostgreSQL Database: Active pool connections, Idle pool connections, Flyway Schema Version (`V6`).
     - Redis Cache: Ping latency, Memory usage.
  2. **Multi-Tenant Health Summary**: Total registered users, total trading accounts, active algo trading accounts, locked accounts.
  3. **Provider Status Matrix**: Delta India REST/WS, CryptoCompare News, Faireconomy Calendar sync indicators.
- **Backend API**: `GET /api/v1/developer/status`
- **States**:
  - *Normal*: All services glowing Green (`HEALTHY`).
  - *Degraded*: Amber indicator with specific failing component highlighted.

---

### Page 2: Multi-Tenant Accounts & Engine State Inspector (`/developer/accounts`)
- **Purpose**: Operational explorer for all trading accounts and engine locks across the platform.
- **Features**:
  - Searchable, paginated account table: Account ID, Account Name, User ID, Exchange Status (`CONNECTED` / `DISCONNECTED`), Algo State (`ENABLED` / `DISABLED`), Kill-Switch State (`ACTIVE` / `INACTIVE`).
  - **Active Trade Lock Inspector**: Shows currently acquired single-trade locks (`active_trade_locks`), setup ID, symbol, lock state, and duration in seconds.
  - Quick action to view account health diagnostics.
- **Backend API**: `GET /api/v1/developer/system/accounts`

---

### Page 3: External Provider Health Monitor (`/developer/providers`)
- **Purpose**: Real-time telemetry for all external market data and intelligence feeds.
- **Feeds Monitored**:
  1. **Delta Exchange India Market Feed**:
     - Endpoint: `https://api.india.delta.exchange`
     - Status: Connection state, candle stream latency, product list cache age.
     - API: `GET /api/v1/market/status?symbol=BTCUSD`
  2. **CryptoCompare Financial News Provider**:
     - Endpoint: `https://min-api.cryptocompare.com/data/v2/news/`
     - Telemetry: Provider name (`LiveFinancialNewsProvider`), last attempted sync, last successful sync, total articles ingested, SHA-256 deduplication count, error logs.
     - API: `GET /api/v1/news/status`
  3. **ForexFactory / Faireconomy Macroeconomic Calendar Provider**:
     - Endpoint: `https://nfs.faireconomy.media/ff_calendar_*.json`
     - Telemetry: Provider name (`LiveMacroEconomicCalendarProvider`), last sync timestamp, total synchronized events, 15-day rolling window range.
     - API: `GET /api/v1/economic-events/status`

---

### Page 4: API Diagnostics & Latency Benchmark (`/developer/diagnostics`)
- **Purpose**: Diagnostic network probing tool for internal and external endpoints.
- **Features**:
  - Live roundtrip latency benchmarks (Backend ↔ PostgreSQL, Backend ↔ Python Engine, Backend ↔ Delta Exchange REST API).
  - Rate limit headroom counters and remaining quota.
  - Active thread pool statistics (`HikariCP`, `TaskScheduler`).
- **Backend API**: `GET /api/v1/developer/diagnostics`

---

### Page 5: Sanitized Real-Time Log Stream (`/developer/logs`)
- **Purpose**: Live terminal-style log inspector with automated credential redaction.
- **Security & Redaction Features**:
  - Automatically sanitizes API keys, secrets, passwords, encryption keys, and JWT tokens (`api_key=***REDACTED***`).
  - Color-coded severity tags (`INFO` Slate, `WARN` Amber, `ERROR` Rose, `SECURITY` Purple).
  - Instant text filter and log level selector (`ALL`, `WARN+ERROR`, `SECURITY_ONLY`).
- **Backend API**: `GET /api/v1/developer/logs`

---

### Page 6: Strategy Sandbox & Market Tick Lab (`/developer/sandbox`)
- **Purpose**: Isolated simulation lab for quant developers to test SMC setup qualification and AI confidence scoring without executing live exchange orders.
- **Features**:
  - **Simulate Price Tick Form**: Input Symbol (`BTCUSD`), Custom Price (`65250.00`), Timeframe (`1h`).
  - **Qualification Result Visualizer**: Shows whether simulated price triggered an Order Block retrace, structure BOS/CHOCH alignment, and resulting confidence score.
  - Sandbox mode indicator: Stamped with clear `[SIMULATION ONLY — ZERO REAL ORDER EXECUTION]` badge.
- **Backend APIs**:
  - `GET /api/v1/developer/sandbox/info`
  - `POST /api/v1/developer/sandbox/simulate-tick`

---

## 3. Data Visibility & Redaction Rules

| Data Category | Visibility in Developer App | Redaction / Masking Rule |
| :--- | :--- | :--- |
| **User ID & Email** | Visible | Displayed for administrative auditing |
| **Password Hashes** | ❌ NEVER EXPOSED | Excluded at JPA and DTO layers |
| **Delta API Keys** | Masked (`api_key_****...`) | Only first 4 characters displayed |
| **Delta API Secrets** | ❌ NEVER EXPOSED | Encrypted AES-256 server-side |
| **Internal Engine Token** | ❌ NEVER EXPOSED | Managed exclusively via server env vars |
| **Account Balances** | Visible | Required for capital allocation audit |
| **Order & Fill Records** | Visible | Required for execution latency analysis |
| **Strategy Setup IDs** | Visible | Required for deterministic signal tracing |
| **Active Trade Locks** | Visible | Required for lock starvation troubleshooting |
