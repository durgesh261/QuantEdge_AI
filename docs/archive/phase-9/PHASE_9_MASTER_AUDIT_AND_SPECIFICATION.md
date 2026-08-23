# QuantEdge AI — Phase 9 Master Audit, Architecture & UI/UX Specification
## Complete Combined Pre-Implementation Blueprint & System Documentation

---

# Table of Contents
1. [Part 1: Deep System Audit & Data Flow Tracing](#part-1-deep-system-audit--data-flow-tracing)
2. [Part 2: Production User App UI/UX Specification](#part-2-production-user-app-uiux-specification)
3. [Part 3: Dedicated Developer App Specification](#part-3-dedicated-developer-app-specification)
4. [Part 4: Backend API Inventory & Gap Analysis](#part-4-backend-api-inventory--gap-analysis)
5. [Part 5: Database Schema & Entity Data Map (Flyway V1–V6)](#part-5-database-schema--entity-data-map-flyway-v1v6)
6. [Part 6: Phased Implementation Roadmap & Rollout Strategy](#part-6-phased-implementation-roadmap--rollout-strategy)
7. [Part 7: Security Architecture & RBAC Audit](#part-7-security-architecture--rbac-audit)
8. [Part 8: Final Target Architecture & Prioritization Matrix](#part-8-final-target-architecture--prioritization-matrix)

---

# Part 1: Deep System Audit & Data Flow Tracing

## 1. Executive Summary
This section presents a comprehensive, read-only architectural audit of the complete QuantEdge AI codebase across all layers:
- **Backend**: Spring Boot 3.4.1 (Java 21), Spring Data JPA, Flyway, Spring Security with JWT.
- **Engine**: Deterministic Python SMC Engine, 1H canonical stream, Rule-Based AI Intelligence Enricher.
- **Database**: PostgreSQL 16 with Flyway migrations V1 through V6.
- **Legacy Frontend**: React 18, Vite 5, TailwindCSS (preserved temporarily as a reference).
- **External Feeds**: Delta Exchange India (`DELTAIN`), CryptoCompare News API, ForexFactory / Faireconomy Macroeconomic Calendar API.

## 2. End-to-End Data Flow Tracing

### 2.1 Flow 1: Database → Entity → Repository → Service → Controller → Frontend
```
PostgreSQL Table
    ↓ (JPA Mapping)
Java Entity (@Entity)
    ↓ (Spring Data JPA)
Repository (JpaRepository)
    ↓ (Business Logic & Tenancy Enforcement)
Service Layer (@Service)
    ↓ (Sanitized DTO Conversion)
REST Controller (@RestController)
    ↓ (HTTPS JSON / HttpOnly Cookie Auth)
Frontend Client (TanStack Query / Zustand)
```
- **Example — Orders**:
  `orders` table → `Order.java` → `OrderRepository.java` → `TradingQueryService.java` (filters by authenticated `user_id`) → `TradeExecutionController.java` (`GET /api/v1/trade/orders`) → `user-app` Order History table.
- **Example — Live News**:
  `news_articles` table (7-day TTL) → `NewsArticle.java` → `NewsArticleRepository.java` → `NewsIngestionService.java` → `NewsController.java` (`GET /api/v1/news`) → `user-app` Live News Feed.
- **Example — Macro Events**:
  `economic_events` table (24-hour post-event TTL) → `EconomicEvent.java` → `EconomicEventRepository.java` → `EconomicCalendarService.java` → `EconomicCalendarController.java` (`GET /api/v1/economic-events`) → `user-app` 15-Day Macroeconomic Calendar.

### 2.2 Flow 2: Market Data → Python Engine → SMC → Strategy Qualification → AI Enrichment → Backend → Apps
```
Delta Exchange India REST & WebSocket
    ↓
Python Market Orchestrator (1H OHLCV Candles)
    ↓
Frozen SMC Core:
    ├── structure.py (Swing Highs/Lows, Trend, BOS, CHOCH)
    ├── order_blocks.py (Bullish & Bearish OBs, Volume, Mitigation)
    ├── fvg.py (Imbalances & Fair Value Gaps)
    ├── liquidity.py (Buy/Sell Liquidity Pools, Equal Highs/Lows)
    └── volatility.py (ATR Dynamic Buffers)
    ↓
Strategy Engine (strategy/engine.py):
    ├── Filters: Price in OB + Structure Alignment + RR >= 2.0
    └── Generates Deterministic setup_id (e.g., setup_BTCUSD_H1_BULLISH_OB_1787400000)
    ↓
AI Intelligence Layer (ai/engine.py & ai/enricher.py):
    ├── Computes Technical Score (0–100)
    ├── Computes Market Regime Score (0–100)
    ├── Computes Sentiment & Macro Risk Modifiers (-15 to +10)
    └── Produces Composite Confidence Score (0–100%) & Plain-English Reasoning
    ↓
Backend Ingestion & Persistence:
    └── Stored in PostgreSQL (strategy_setups, ai_signal_enrichments)
    ↓
Delivery to Applications:
    ├── User App: Live Trading Terminal, SMC Overlays, AI Signal Radar
    └── Developer App: Engine State Inspector, Setup Lifecycle Tracker
```

### 2.3 Flow 3: User → Auth → Account → Risk Config → Order → Fill → Position → Trade Record
```
User Authentication:
    ├── AuthController (/api/v1/auth/login) ──> BCrypt Password Verification
    └── Generates HttpOnly JWT Access & Refresh Cookies
    ↓
Exchange Connectivity:
    ├── AccountController (/api/v1/account/connect) ──> AES-256 Encrypted Delta API Key/Secret
    └── Stored in delta_connections table (never exposed in responses)
    ↓
Risk Configuration:
    └── risk_configurations table (Max Risk % per trade, Max Leverage, Daily Loss Limit)
    ↓
Signal Qualification & Order Placement:
    ├── Signal arrives from 1H SMC Pipeline
    ├── OrderExecutionService validates Preconditions:
    │   ├── Account is Active & Verified
    │   ├── Algo Trading is ENABLED
    │   ├── Emergency Kill-Switch is INACTIVE
    │   └── No conflicting Active Trade Lock on Account
    ├── Calculates Position Size & Margin via Capital Allocator
    └── Dispatches real order to Delta Exchange India:
        └── SOLE REAL ORDER AUTHORITY: OrderExecutionService.java:312 (POST /v2/orders)
    ↓
Execution Lifecycle:
    ├── Order recorded in orders table (PENDING -> OPEN)
    ├── Fills received and recorded in order_fills table
    ├── Open position tracked in positions table with live unrealized P&L
    └── When Position Closes:
        ├── Position status set to CLOSED
        └── Immutable record created in trade_records table with realized Net P&L & fees
```

## 3. Real Order Authority Verification
- **Sole Location**: `backend/src/main/java/com/quantedge/trading/service/OrderExecutionService.java` at line 312.
- **Search Verification**: `POST /v2/orders` is called **exclusively** within `OrderExecutionService.java`.
- **Zero Execution in Other Modules**:
  - `MarketDataController` & `DeltaMarketDataClient`: Strictly read-only public market data.
  - `NewsController` & `ExternalFinancialNewsProvider`: Strictly read-only public news ingestion.
  - `EconomicCalendarController` & `ExternalEconomicCalendarProvider`: Strictly read-only economic event synchronization.
  - `NotificationController` & `NotificationService`: In-app alerts only.
  - `AiIntelligenceController` & `AiEnrichmentService`: Analytical scoring only.

## 4. Frozen SMC Core Verification
The following 3 algorithmic core files are strictly frozen:
1. `engine/src/quantedge/smc/structure.py`
2. `engine/src/quantedge/smc/order_blocks.py`
3. `engine/src/quantedge/smc/volatility.py`

**Diff against `origin/main`**: **ZERO DIFF**.

---

# Part 2: Production User App UI/UX Specification

## 1. Product Overview & User Persona
The **QuantEdge User App** (`user-app/`) is an institutional-grade algorithmic trading web application built for quantitative traders, discretionary algorithmic traders, and portfolio managers.

## 2. Complete Page-by-Page Inventory & UI/UX Blueprints

### Page 1: Secure Authentication (`/login` & `/signup`)
- **Purpose**: Authenticate users and establish secure HttpOnly JWT session cookies.
- **Components**: AuthCard, FormInput, PasswordField, SubmitButton, SecurityNotice.
- **Backend APIs**: `POST /api/v1/auth/login`, `POST /api/v1/auth/signup`
- **States**:
  - *Loading*: Disabled button with spinner (`Authenticating...`).
  - *Error*: Inline badge (`Invalid credentials or account inactive`).
  - *Success*: Instant redirect to `/`.

### Page 2: Executive Trading Dashboard (`/`)
- **Purpose**: High-level command overview of account balance, algorithmic status, recent signals, and portfolio performance.
- **Layout**: 4-Metric Top Row + 2-Column Split (Live Radar & Active Positions / Market Feed).
- **Widgets**:
  1. **Portfolio Stat Cards**: Total Equity, 24h P&L ($ / %), Active Positions count, Margin Utilization %.
  2. **Algo Health Banner**: Connection status (`CONNECTED` / `DISCONNECTED`), Algo state (`ENABLED` / `DISABLED`), Active Trade Lock indicator.
  3. **Recent Signals Widget**: Top qualified SMC setups with AI confidence badges.
  4. **Active Positions Snapshot**: Mini position rows with quick close actions.
  5. **Breaking News Marquee**: Top 3 high-importance market headlines.
- **Backend APIs**:
  - `GET /api/v1/account/summary`
  - `GET /api/v1/trade/status`
  - `GET /api/v1/trade/positions`
  - `GET /api/v1/ai/enrichments?limit=5`
  - `GET /api/v1/news?importance=HIGH&limit=3`

### Page 3: Advanced Trading Terminal (`/terminal`)
- **Purpose**: The primary workstation combining TradingView charts, SMC overlays, order flow, active positions, and quick execution controls.
- **Layout**: 3-Pane Desktop Layout:
  - **Left / Center (65% width)**: Symbol selector + Timeframe bar + Lightweight Charts Canvas + Bottom Tabbed Tray (Positions / Open Orders / Fills / Strategy Setups).
  - **Right Sidebar (35% width)**: AI Signal Radar & Reasoning + Order Ticket + Market Depth & Ticker Summary.
- **Detailed Component Specifications**:
  1. **TradingView / Lightweight Candlestick Canvas**:
     - 1H canonical timeframe (with 1m, 5m, 15m, 4h, 1d view toggles).
     - Green/Red candlesticks with volume histogram below.
     - **SMC Visual Layer**:
       - *Bullish Order Blocks*: Semi-transparent green rectangle (`rgba(16, 185, 129, 0.18)`) with dotted upper/lower bounds and label: `Bullish OB (H1)`.
       - *Bearish Order Blocks*: Semi-transparent red rectangle (`rgba(244, 63, 94, 0.18)`) with dotted bounds and label: `Bearish OB (H1)`.
       - *BOS / CHOCH Break Lines*: Dotted horizontal line with cyan pill badge: `BOS ▲` or `CHOCH ▼`.
       - *Fair Value Gaps (FVG)*: Vertical hatched highlight band.
       - *Trade Markers*: Dashed horizontal lines for Entry (`Cyan`), Stop Loss (`Red`), Take Profit 1 & 2 (`Green`).
  2. **AI Signal Radar Card**:
     - Circular progress meter displaying **Composite Confidence Score** (e.g. `84%`).
     - Breakdown sub-meters: Technical Alignment (`90%`), Market Regime (`85%`), Macro Factor (`-5%`).
     - Plain-English AI analysis: *"Strong bullish structure alignment inside 1H unmitigated demand zone. Macro risk moderate due to upcoming CPI."*
  3. **Order Ticket (Manual / Algo Assisted)**:
     - Side selector: `BUY / LONG` (Green) vs `SELL / SHORT` (Red).
     - Order Type: `LIMIT`, `MARKET`, `STOP_LIMIT`.
     - Quantity input with balance percentage shortcuts (`25%`, `50%`, `75%`, `100%`).
     - Leverage slider (`1x` to `100x`, constrained by user risk settings).
     - Auto SL/TP calculation based on active Order Block bounds.
     - Dangerous Action Protection: Double-confirmation modal for manual market orders.
- **Backend APIs**:
  - `GET /api/v1/market/candles?symbol=BTCUSD&interval=1h`
  - `GET /api/v1/market/ticker/BTCUSD`
  - `GET /api/v1/trade/positions`
  - `GET /api/v1/trade/orders?status=OPEN`
  - `GET /api/v1/trade/signals?symbol=BTCUSD`
  - `GET /api/v1/ai/enrichments/BTCUSD`

### Page 4: Strategy Setups & AI Signal Radar (`/signals`)
- **Purpose**: Dedicated explorer for all algorithmically identified and qualified SMC trade setups across monitored markets.
- **Components**:
  - Filter bar: Symbol (`BTCUSD`, `ETHUSD`, `SOLUSD`), Direction (`LONG`, `SHORT`), Status (`QUALIFIED`, `ACTIVE`, `INVALIDATED`, `COMPLETED`).
  - Signal Grid Cards: Setup ID badge, Entry Price, Stop Loss, Take Profit 1 & 2, RR Ratio, AI Composite Confidence Badge, Status pill.
- **Backend APIs**: `GET /api/v1/trade/signals`, `GET /api/v1/ai/enrichments/{setupId}`

### Page 5: Live Market Intelligence (`/intelligence`)
- **Purpose**: Unified intelligence portal combining categorized financial news and the 15-day macroeconomic calendar.
- **Layout**: Two Split Tabs:
  1. **Financial & Crypto News Feed**: Category filter pills, Sentiment badges (`BULLISH`, `BEARISH`, `NEUTRAL`), Importance tags (`CRITICAL`, `HIGH`), Strict 7-day retention tag (`Expires in X days`), Canonical source link.
  2. **15-Day Macroeconomic Calendar**: Grouped chronologically by date, Live countdown badge (`in 3h 24m`), Country flag badge (`US`, `IN`, `EU`, `GB`, `JP`, `CN`, `CA`, `AU`), Impact pill (`HIGH`, `MEDIUM`, `LOW`), Comparison table (Previous, Forecast, Actual), Strict 24-hour post-event retention indicator.
- **Backend APIs**: `GET /api/v1/news`, `GET /api/v1/economic-events`

### Page 6: Orders & Fills Ledger (`/orders`)
- **Purpose**: Comprehensive audit ledger for all open, filled, cancelled, and rejected orders.
- **Components**: Tab 1 (Open Orders), Tab 2 (Order History), Tab 3 (Execution Fills with fee and slippage breakdown).
- **Backend APIs**: `GET /api/v1/trade/orders`, `GET /api/v1/trade/fills`

### Page 7: Positions & Realized P&L (`/positions`)
- **Purpose**: Active position monitor and closed trade performance ledger.
- **Components**: Open Positions Table with live mark price and unrealized P&L, Closed Trades History Table with net realized P&L, fees, and exit reasons.
- **Backend APIs**: `GET /api/v1/trade/positions`, `GET /api/v1/trade/history`

### Page 8: Risk Management & Algo Controls (`/risk-algo`)
- **Purpose**: Master control station for algorithmic trading rules, capital allocation, and emergency risk switches.
- **Components**: Large **EMERGENCY KILL-SWITCH** button, Algo Master Switch (`ENABLED` / `DISABLED`), Risk Configuration Form (Max Risk %, Max Leverage, Daily Loss Limit).
- **Backend APIs**: `GET /api/v1/account/algo-config`, `PUT /api/v1/account/algo-config`, `POST /api/v1/trade/algo/toggle`, `POST /api/v1/trade/kill-switch`, `POST /api/v1/trade/kill-switch/reset`

### Page 9: Account Settings & Exchange Keys (`/settings`)
- **Purpose**: Delta Exchange India API key configuration and security preferences.
- **Components**: Delta Exchange Connection Form (AES-256 encrypted), Account Verification Tool (balance probe without trading), Security Card.
- **Backend APIs**: `POST /api/v1/account/connect`, `POST /api/v1/account/verify`, `POST /api/v1/account/disconnect`, `GET /api/v1/account/status`

### Page 10: In-App Notification Drawer (`/notifications` & Drawer)
- **Purpose**: Real-time alert center for critical market events, trade executions, and economic releases.
- **Features**: Dropdown drawer + full-page view, Severity tags (`CRITICAL`, `HIGH`, `INFO`), Mark as Read actions.
- **Backend APIs**: `GET /api/v1/notifications`, `POST /api/v1/notifications/{id}/read`, `POST /api/v1/notifications/read-all`

---

# Part 3: Dedicated Developer App Specification

## 1. Product Overview & Developer Persona
The **QuantEdge Developer App** (`developer-app/`) is an administrative and engineering workstation designed for quantitative analysts, systems engineers, and operations administrators with role `ROLE_DEVELOPER` or `ROLE_ADMIN`.

## 2. Complete Page-by-Page Inventory & UI/UX Blueprints

### Page 1: Developer Command Center (`/developer`)
- **Purpose**: Executive operational telemetry dashboard displaying platform vitals and service status.
- **Widgets**:
  1. **Core Service Vitals**: Spring Boot Backend (JVM Heap, Uptime, Threads), Python SMC Engine (Bridge status, Heartbeat), PostgreSQL (Active/Idle pool connections, Flyway V6), Redis Cache.
  2. **Multi-Tenant Health Summary**: Total registered users, total accounts, active algo accounts, locked accounts.
  3. **Provider Status Matrix**: Delta India REST/WS, CryptoCompare News, Faireconomy Calendar sync indicators.
- **Backend API**: `GET /api/v1/developer/status`

### Page 2: Multi-Tenant Accounts & Engine State Inspector (`/developer/accounts`)
- **Purpose**: Operational explorer for all trading accounts and engine locks across the platform.
- **Features**: Searchable account table, Active trade lock inspector (`active_trade_locks`), Open orders count, Error state indicators.
- **Backend API**: `GET /api/v1/developer/system/accounts`

### Page 3: External Provider Health Monitor (`/developer/providers`)
- **Purpose**: Real-time telemetry for all external market data and intelligence feeds.
- **Feeds Monitored**:
  1. **Delta Exchange India Market Feed**: `GET /api/v1/market/status?symbol=BTCUSD`
  2. **CryptoCompare Financial News Provider**: `GET /api/v1/news/status`
  3. **ForexFactory / Faireconomy Macroeconomic Calendar Provider**: `GET /api/v1/economic-events/status`

### Page 4: API Diagnostics & Latency Benchmark (`/developer/diagnostics`)
- **Purpose**: Diagnostic network probing tool for internal and external endpoints.
- **Features**: Live roundtrip latency benchmarks (Backend ↔ DB, Backend ↔ Engine, Backend ↔ Delta API), rate limit headroom counters.
- **Backend API**: `GET /api/v1/developer/diagnostics`

### Page 5: Sanitized Real-Time Log Stream (`/developer/logs`)
- **Purpose**: Live terminal-style log inspector with automated credential redaction.
- **Security Features**: Automatically sanitizes API keys, secrets, passwords, encryption keys, and JWT tokens (`api_key=***REDACTED***`). Severity coloring (`INFO`, `WARN`, `ERROR`, `SECURITY`).
- **Backend API**: `GET /api/v1/developer/logs`

### Page 6: Strategy Sandbox & Market Tick Lab (`/developer/sandbox`)
- **Purpose**: Isolated simulation lab for quant developers to test SMC setup qualification and AI confidence scoring without executing live exchange orders.
- **Features**: Price tick simulator form (`POST /api/v1/developer/sandbox/simulate-tick`), Setup evaluation inspector showing SMC qualification criteria. Stamped with `[SIMULATION ONLY — ZERO REAL ORDER EXECUTION]`.
- **Backend APIs**: `GET /api/v1/developer/sandbox/info`, `POST /api/v1/developer/sandbox/simulate-tick`

---

# Part 4: Backend API Inventory & Gap Analysis

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

# Part 5: Database Schema & Entity Data Map (Flyway V1–V6)

## 1. Schema Migration Chain
- `V1__initial_schema.sql`: Core user authentication, multi-account management, encrypted Delta credentials, risk and strategy configurations, orders, positions, order blocks, journal entries, and audit logs.
- `V2__add_missing_columns.sql`: Strategy setups, active trade locks, trade records.
- `V3__add_user_role.sql`: User roles (`ROLE_USER`, `ROLE_DEVELOPER`, `ROLE_ADMIN`).
- `V4__phase6_state_machine_and_fills.sql`: State machine validation and fill records (`order_fills`).
- `V5__phase7_5_ai_signal_enrichment.sql`: AI intelligence scoring and regime classification (`ai_signal_enrichments`).
- `V6__phase8_market_news_events.sql`: Live news articles (7-day TTL), economic events (24-hour TTL), and in-app notifications (`notification_events`).

## 2. Table Ownership & Retention Summary

| Table Name | Owner Module | Retention Rule | Tenancy Isolation |
| :--- | :--- | :--- | :--- |
| `users` | Auth | Permanent | Self |
| `trading_accounts` | Account | Permanent | `user_id` FK |
| `delta_connections` | Account/Security | Retained until disconnected | `account_id` FK (AES-256 encrypted) |
| `risk_configurations` | Risk | Permanent | `account_id` FK |
| `strategy_configurations` | Strategy | Permanent | `account_id` FK |
| `orders` | Trading | Permanent | `account_id` FK |
| `order_fills` | Trading | Permanent | `order_id` & `account_id` FK |
| `positions` | Trading | Permanent | `account_id` FK |
| `trade_records` | Performance | Permanent | `account_id` FK |
| `active_trade_locks` | Engine/Trading | Released upon trade exit | `account_id` FK |
| `strategy_setups` | Strategy | Permanent | `account_id` FK |
| `ai_signal_enrichments` | AI Intelligence | Permanent | `account_id` FK |
| `news_articles` | News Ingestion | **Strict 7-Day TTL** | Global Public |
| `economic_events` | Economic Calendar | **Strict 24-Hour Post-Event TTL** | Global Public |
| `notification_events` | Notification | 30-Day TTL | `user_id` FK |
| `audit_logs` | Security/Dev | Permanent | `user_id` FK (Nullable) |

---

# Part 6: Phased Implementation Roadmap & Rollout Strategy

```
Phase 9A: Deep Repository Audit (COMPLETE)
Phase 9B: User App Architecture & UI/UX Design (COMPLETE)
Phase 9C: Developer App Architecture & Telemetry Spec (COMPLETE)
Phase 9D: API / Data / Security Gap Analysis (COMPLETE)
                    │
                    ▼
═══════════════════════════════════════════════════════════════════════════════
       🔴 [ USER APPROVAL GATE — MUST OBTAIN EXPLICIT APPROVAL ]
═══════════════════════════════════════════════════════════════════════════════
                    │
                    ▼
Phase 9E: Scaffold & Implement User App (user-app/)
├── Step 1: Vite + React 18 + TypeScript + Tailwind CSS Design System
├── Step 2: Auth Store (JWT Cookies), Layout Shell, Header Tickers
├── Step 3: TradingView / Lightweight Charts Engine + SMC Overlays (OB, FVG, BOS/CHOCH)
├── Step 4: Executive Dashboard & Signal Radar Cards
├── Step 5: Live Market Intelligence (Categorized News + 15d Economic Calendar)
├── Step 6: Orders, Positions, Execution Fills, and Realized P&L Ledger
└── Step 7: Risk Management Panel, Algo Toggle, and Emergency Kill-Switch

Phase 9F: Scaffold & Implement Developer App (developer-app/)
├── Step 1: Vite + React 18 + TypeScript + Standalone Dev Layout Shell
├── Step 2: RBAC Gate (ROLE_DEVELOPER, ROLE_ADMIN) + 403 Redirection
├── Step 3: Developer Command Center & Platform Vitals Telemetry
├── Step 4: Multi-Tenant Account Health & Active Trade Lock Inspector
├── Step 5: External Provider Sync Telemetry (Delta, CryptoCompare, Faireconomy)
├── Step 6: Sanitized Real-Time Log Viewer Terminal & Latency Prober
└── Step 7: Strategy Sandbox Lab & Simulated Price Tick Runner

Phase 9G: End-to-End Integration Testing & Feature Parity Comparison
├── Automated Cypress / Playwright E2E suites against live Spring Boot backend
├── Feature-by-feature comparison against legacy frontend/
└── Mobile & Tablet responsive breakpoint validation

Phase 9H: Production Build & Security Audit
├── Run npm run build in both user-app/ and developer-app/
├── Verify zero credential leakage and zero bundle cross-contamination
├── Run Backend Maven tests (156 tests) & Python pytest suite (902 tests)
└── Verify frozen SMC core (ZERO DIFF)

═══════════════════════════════════════════════════════════════════════════════
       🔴 [ FINAL USER APPROVAL GATE FOR LEGACY CLEANUP ]
═══════════════════════════════════════════════════════════════════════════════
                    │
                    ▼
Phase 9I: Deprecate Legacy Frontend
└── Update root docker-compose.yml to serve user-app (Port 3000) and developer-app (Port 3001)

Phase 9J: Clean Removal of Legacy frontend/ Directory
```

---

# Part 7: Security Architecture & RBAC Audit

## 1. Authentication & Session Architecture
- **Stateless JWT**: HttpOnly, SameSite=Lax cookies for both `access_token` (24h) and `refresh_token` (7d).
- **Password Security**: BCrypt strength 12.
- **Tenant Isolation**: Every database query explicitly verifies `trading_accounts.user_id = authenticated_user.id`.
- **Credential Protection**: Delta API keys/secrets are encrypted via AES-256-GCM server-side and never returned in plain text.
- **Developer Redaction**: Automatic regex scrubbing masks passwords, tokens, and secrets (`***REDACTED***`).

---

# Part 8: Final Target Architecture & Prioritization Matrix

## 1. Unified Repository & Application Structure
```
QuantEdge-AI/
├── backend/               # Spring Boot 3.4.1 Backend
├── engine/                # Frozen Python SMC Engine & AI Enricher
├── user-app/              # [PHASE 9 TARGET] Production User Trading Web App (Port 3000)
├── developer-app/         # [PHASE 9 TARGET] Standalone Developer Observability Console (Port 3001)
├── frontend/              # [LEGACY REFERENCE — PRESERVED UNTOUCHED]
└── docker-compose.yml     # Unified multi-container orchestration
```

## 2. Feature Prioritization Matrix

| Feature Area | Module / Page | Priority | Dependencies | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Authentication** | User App: `/login`, `/signup` | **P0** | `FRONTEND ONLY` | Secure HttpOnly JWT session lifecycle |
| **Trading Terminal** | User App: `/terminal` | **P0** | `FRONTEND ONLY` | TradingView canvas with SMC visual overlays |
| **Order & Risk Controls**| User App: `/risk-algo`, Header | **P0** | `FRONTEND ONLY` | Emergency Kill-Switch and Algo Trading master toggle |
| **Signals & AI Radar** | User App: `/signals` | **P1** | `FRONTEND ONLY` | SMC setups explorer and AI confidence gauge |
| **Orders & Positions** | User App: `/orders`, `/positions` | **P1** | `FRONTEND ONLY` | Live open orders, fills ledger, and realized P&L |
| **Market Intelligence** | User App: `/intelligence` | **P1** | `FRONTEND ONLY` | Categorized news feed and 15-day macro calendar |
| **Developer Telemetry** | Dev App: `/developer` | **P1** | `FRONTEND ONLY` | JVM, Engine, Database, and Account vitals |
| **Provider Monitors** | Dev App: `/developer/providers` | **P1** | `FRONTEND ONLY` | Delta, CryptoCompare, and Faireconomy health |
| **Sandbox Lab** | Dev App: `/developer/sandbox` | **P2** | `FRONTEND ONLY` | Simulated market price tick runner |
| **Sanitized Logs** | Dev App: `/developer/logs` | **P2** | `FRONTEND ONLY` | Real-time terminal log viewer with key redaction |
| **Admin Lock Release** | Dev App: `/developer/accounts` | **P3** | `BACKEND REQUIRED` | Optional manual active trade lock release action |
| **Migration History** | Dev App: `/developer/diagnostics`| **P3** | `BACKEND REQUIRED` | Optional Flyway migration history inspector |

---

# Final Pre-Implementation Verification Checklist

| Invariant / Verification Gate | Status | Evidence |
| :--- | :--- | :--- |
| **Frontend Coding Halted** | **CONFIRMED** | Zero UI/UX implementation files created. |
| **Legacy Frontend Preserved** | **CONFIRMED** | `frontend/` directory is 100% untouched and functional. |
| **SMC Core Frozen** | **ZERO DIFF** | `structure.py`, `order_blocks.py`, `volatility.py` match `origin/main` exactly. |
| **Real-Order Authority** | **INVARIANT PRESERVED** | `POST /v2/orders` exists **exclusively** at `OrderExecutionService.java:312`. |
| **Single Database Authority** | **CONFIRMED** | Single PostgreSQL 16 schema (Flyway V1–V6); zero database duplication. |
| **Git Repository State** | **CLEAN** | `HEAD` (`24b43c4`) is synchronized with `origin/main`. |

---

# **PHASE 9 PRE-IMPLEMENTATION AUDIT COMPLETE — AWAITING USER APPROVAL.**
