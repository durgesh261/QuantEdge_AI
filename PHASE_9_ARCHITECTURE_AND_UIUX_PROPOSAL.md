# QuantEdge AI — Phase 9 Architecture & UI/UX Proposal
## Comprehensive Read-Only System Audit, Product Blueprint & Dual-Application Architecture

---

## 1. Executive Summary & Audit Context

This document constitutes the formal architectural and product blueprint for **Phase 9: Dual Frontend System (Production User Trading Web App & Dedicated Developer Observability App)**.

Following the successful completion and production hardening of **Phase 8.3**, the QuantEdge AI backend, deterministic SMC engine, live Delta Exchange India (`DELTAIN`) market feed, AI intelligence layer, external financial news pipeline, and macroeconomic calendar backend are fully production-grade, authoritative, and verified.

### Core Architectural Guarantees Maintained
1. **Zero Destructive Actions**: The existing frontend (`frontend/`) remains 100% untouched and functional during planning and early development.
2. **SMC Core Frozen**: `structure.py`, `order_blocks.py`, `volatility.py` remain strictly untouched with **ZERO DIFF**.
3. **Sole Real-Order Authority**: `OrderExecutionService.java:312` remains the **exclusive** authority for real Delta Exchange order execution (`POST /v2/orders`).
4. **Single Source of Truth**: PostgreSQL 16 (Flyway V1–V6) is the authoritative database. **Zero database duplication** will occur between the User App and the Developer App.

---

## 2. Complete Repository Architecture & System Map

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     QUANTEDGE AI TOPOLOGY                                   │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

    ┌───────────────────────────────────┐               ┌───────────────────────────────────┐
    │     Production User Trading App   │               │     Dedicated Developer App       │
    │     (React 18 / TypeScript / TV)  │               │   (React 18 / Observability Lab)  │
    │      Route: / (Port 3000)         │               │     Route: /developer (Port 3001) │
    └─────────────────┬─────────────────┘               └─────────────────┬─────────────────┘
                      │                                                   │
                      │  REST / JWT Auth Cookies / WebSocket Streams      │  REST / RBAC ROLE_DEVELOPER / Admin
                      └─────────────────────────┬─────────────────────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                             SPRING BOOT 3.4.1 BACKEND (Port 8080)                           │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  REST API Controllers:                                                                      │
│  ├── AuthController (/api/v1/auth) ──> JWT HttpOnly Cookies, BCrypt, User Tenancy            │
│  ├── AccountController (/api/v1/account) ──> AES-256 Key Encryption, Balance/Margin Sync     │
│  ├── TradeExecutionController (/api/v1/trade) ──> Kill-Switch, Algo Toggle, Orders, Fills    │
│  ├── MarketDataController (/api/v1/market) ──> Delta Exchange India Candles & Tickers        │
│  ├── NewsController (/api/v1/news) ──> Categorized Live News, SHA-256 Deduplication, 7d Ret │
│  ├── EconomicCalendarController (/api/v1/economic-events) ──> 15d Window, UTC, 24h Ret      │
│  ├── NotificationController (/api/v1/notifications) ──> In-App Alerts, Read State            │
│  ├── AiIntelligenceController (/api/v1/ai) ──> Signal Confidence, Regime, Macro Risk         │
│  ├── DeveloperController (/api/v1/developer) ──> Diagnostic Telemetry, System Health, Logs   │
│  └── EngineStateController (/api/engine) ──> Python Engine Bridge (X-Engine-Api-Key)        │
│                                                                                             │
│  Authoritative Trading & Risk Layer:                                                        │
│  └── OrderExecutionService ──[ SOLE REAL ORDER AUTHORITY: POST /v2/orders ]──► Delta India │
└───────────────────────┬─────────────────────────────────────────────┬───────────────────────┘
                        │                                             │
                        ▼                                             ▼
┌───────────────────────────────────────────────┐   ┌─────────────────────────────────────────┐
│           POSTGRESQL 16 (Port 5432)           │   │       PYTHON SMC ENGINE (Port 8000)     │
├───────────────────────────────────────────────┤   ├─────────────────────────────────────────┤
│  Flyway Migrations: V1 -> V6                  │   │  ├── SMC Pipeline (1H Timeframe)        │
│  ├── users, user_settings                     │   │  │   ├── Market Structure (BOS / CHOCH) │
│  ├── trading_accounts, delta_connections      │   │  │   ├── Order Blocks & Mitigation      │
│  ├── risk_configurations, strategy_configs    │   │  │   ├── FVG, Liquidity & Equal Levels  │
│  ├── orders, positions, trade_records, fills  │   │  │   └── Strategy Qualification (RR)    │
│  ├── strategy_setups, active_trade_locks      │   │  ├── AI Intelligence & Signal Enricher  │
│  ├── ai_signal_enrichments                    │   │  └── Delta India Market Data Feed       │
│  ├── news_articles (7-day retention)          │   └─────────────────────────────────────────┘
│  ├── economic_events (24-hour retention)      │
│  └── notification_events, audit_logs          │
└───────────────────────────────────────────────┘
```

---

## 3. Database & Data Ownership Audit

### 3.1 Flyway Migration History & Schema Integrity
- **`V1__initial_schema.sql`**: Core user authentication (`users`, `user_settings`), multi-account management (`trading_accounts`), encrypted Delta credentials (`delta_connections`), risk and strategy configurations (`risk_configurations`, `strategy_configurations`), orders, positions, order blocks, journal entries, and audit logs.
- **`V2__add_missing_columns.sql`**: Production algorithmic state machine (`strategy_setups`, `active_trade_locks`, `trade_records`).
- **`V3__add_user_role.sql`**: Role-based access control (`ROLE_USER`, `ROLE_DEVELOPER`, `ROLE_ADMIN`).
- **`V4__phase6_state_machine_and_fills.sql`**: State machine validation constraints and fill-level audit tracking (`order_fills`).
- **`V5__phase7_5_ai_signal_enrichment.sql`**: AI intelligence scoring, regime classification, and macro risk records (`ai_signal_enrichments`).
- **`V6__phase8_market_news_events.sql`**: Live market intelligence tables (`news_articles`, `economic_events`, `notification_events`) with strict SHA-256 deduplication and time-to-live indexes.

### 3.2 Key Entity Relationships & Tenancy Rules
```
User (1) ──< TradingAccount (N) ──< DeltaConnection (1)
                                ──< RiskConfiguration (1)
                                ──< StrategyConfiguration (1)
                                ──< Order (N) ──< OrderFill (N)
                                ──< Position (N)
                                ──< TradeRecord (N)
                                ──< StrategySetup (N) ──< AiSignalEnrichment (1)
                                ──< ActiveTradeLock (N)

User (1) ──< NotificationEvent (N)
User (1) ──< JournalEntry (N)
User (1) ──< AuditLog (N)

Global Unowned Feeds:
├── NewsArticle (7-Day TTL)
└── EconomicEvent (24-Hour Post-Event TTL)
```

### 3.3 Root `database/` Directory Audit
- **Confirmation**: The root `database/` directory is **NOT REQUIRED** and was a legacy placeholder. All Flyway migrations reside in `backend/src/main/resources/db/migration/` and are automatically executed by Spring Boot on startup with `spring.jpa.hibernate.ddl-auto: validate`.

---

## 4. Existing Frontend Audit & Gap Analysis

### 4.1 Current Frontend Inventory (`frontend/src/`)
- **Pages**: `Dashboard`, `LiveTrading`, `Orders`, `Positions`, `Journal`, `Analytics`, `Settings`, `Login`, `Signup`, `DeveloperDashboard`, `SandboxLab`, `ApiDiagnostics`, `LogsViewer`, `SystemHealth`.
- **Strengths**: Solid Tailwind CSS baseline, clean routing with `react-router-dom`, functional connection to account and trade control endpoints.
- **Critical Limitations**:
  1. **Lacks Real Charting**: Uses simulated/static SVG sparklines instead of a live TradingView / Lightweight Charts candlestick engine.
  2. **No SMC Visual Overlays**: Does not render Order Blocks, Fair Value Gaps, BOS/CHOCH breaks, or Liquidity pools on the chart.
  3. **Missing Phase 8 Endpoints**: Does not consume the live Market Data, Financial News, Economic Calendar, or Notification APIs.
  4. **Missing Phase 7.5 AI Insights**: Does not display AI Composite Confidence Scores, Technical Scores, or Regime analysis.
  5. **Coupled User and Developer Pages**: Developer pages live under `/developer` inside the same bundle as the user trading UI, increasing bundle size and conflating concerns.

---

## 5. Dual Application Architecture Plan

To achieve true institutional-grade separation of concerns without database duplication, we propose a **Dual Application Architecture**:

```
QuantEdge AI Repository
├── backend/               # Authoritative Spring Boot 3 Backend
├── engine/                # Frozen Python SMC Engine & AI Enricher
├── frontend/              # (Preserved until full validation)
├── user-app/              # [NEW] High-Performance Production User Trading App
└── developer-app/         # [NEW] Standalone System Observability & Telemetry App
```

### App 1: Production User Trading App (`user-app/`)
- **Audience**: Institutional & Retail Algo Traders.
- **Purpose**: Real-time market analysis, automated strategy monitoring, manual trade execution, risk management, AI signal confidence assessment, breaking financial news, and macroeconomic events.
- **Key Modules**:
  1. **Live Trading Terminal**: Multi-pane layout with TradingView chart, live order book, SMC visual overlays, quick order ticket, and active position management.
  2. **Strategy & AI Radar**: Visualizes qualified SMC setups (`H1`), confidence scores, entry/SL/TP parameters, and AI regime context.
  3. **Live Intelligence Feed**: Real-time filtered financial news stream (7-day retention) and 15-day rolling macroeconomic calendar countdown.
  4. **Positions & Fills Ledger**: Real-time unrealized/realized P&L tracking, fill history, and execution slip analysis.
  5. **Risk & Algo Control Center**: Account margin health, capital allocation, emergency Kill-Switch, and algorithmic toggle.
  6. **Security & Exchange Keys**: Client-side encrypted exchange credential management (AES-256 server storage).

### App 2: Dedicated Developer Observability App (`developer-app/`)
- **Audience**: System Operators, Quant Engineers, and Administrators (`ROLE_DEVELOPER`, `ROLE_ADMIN`).
- **Purpose**: System-wide telemetry, multi-tenant account health inspection, algorithm lock debugging, API latency diagnostics, sandbox tick simulation, and provider sync monitoring.
- **Key Modules**:
  1. **System Health & Telemetry**: Backend JVM metrics, Python engine status, PostgreSQL pool health, Redis cache status.
  2. **Tenant & Account Inspector**: Inspects all accounts, exchange connectivity states, open order counts, and error flags.
  3. **SMC & Engine State Inspector**: Live inspection of active trade locks, lock acquisition timestamps, and strategy setup lifecycle states.
  4. **Provider Sync Monitor**: Real-time health, latency, sync rate, and error counters for Delta India, CryptoCompare, and Faireconomy feeds.
  5. **Interactive Sandbox Lab**: Simulates market price ticks and tests signal qualification pipelines without risking capital.
  6. **Sanitized Audit & Error Stream**: Real-time log inspector with automatic credential and PII redaction.

---

## 6. Security, Authentication & Role Model

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 ROLE-BASED PERMISSION MATRIX                                │
├───────────────────────────────┬───────────────────────────┬─────────────────────────────────┤
│ Endpoint Family               │ ROLE_USER                 │ ROLE_DEVELOPER / ROLE_ADMIN     │
├───────────────────────────────┼───────────────────────────┼─────────────────────────────────┤
│ /api/v1/auth/**               │ Own Profile Only          │ Own Profile Only                │
│ /api/v1/account/**            │ Own Account Only (IDOR)   │ Own Account Only                │
│ /api/v1/trade/**              │ Own Orders/Positions Only │ Own Orders/Positions Only       │
│ /api/v1/market/**             │ Public Read-Only          │ Public Read-Only                │
│ /api/v1/news/**               │ Public Read-Only          │ Public Read-Only + Manual Sync  │
│ /api/v1/economic-events/**    │ Public Read-Only          │ Public Read-Only + Manual Sync  │
│ /api/v1/ai/**                 │ Own Account Signals       │ Own Account Signals             │
│ /api/v1/notifications/**      │ Own Notifications Only    │ Own Notifications Only          │
│ /api/v1/developer/**          │ ❌ 403 FORBIDDEN          │ ✅ Full Access                  │
│ /api/engine/**                │ ❌ 401/403 (Engine Only)  │ ❌ 401/403 (Engine Only)        │
└───────────────────────────────┴───────────────────────────┴─────────────────────────────────┘
```

- **Authentication**: Stateless JWT in `HttpOnly`, `SameSite=Lax` cookies.
- **Tenant Isolation**: Every database query filters by `trading_accounts.user_id = authenticated_user_id`. Attempted cross-tenant access logs an `IDOR attempt detected` security warning and returns HTTP 404/403.
- **Credential Protection**: Delta API keys and secrets are AES-256 encrypted before insertion and never returned in any DTO.

---

## 7. Real Trading Safety & Execution Invariants

The following invariants are hardcoded into the architecture and will be enforced across all frontend implementations:

1. **Sole Order Execution Authority**: Only `OrderExecutionService.java` communicates with Delta Exchange India (`POST /v2/orders`). Neither frontend nor market data modules possess order submission capabilities.
2. **SMC Engine Determinism**: The SMC algorithms (`structure.py`, `order_blocks.py`, `volatility.py`) operate strictly on historical/live 1H candle streams. Frontend actions cannot modify or tamper with algorithmic calculations.
3. **No Direct Database Access**: Both frontend applications communicate strictly via authenticated REST APIs. Zero direct PostgreSQL or Redis connections are permitted from the client.
4. **Emergency Failsafe (Kill Switch)**: Activating the Kill Switch instantly disables the algo trading loop and cancels pending unplaced orders via server-side atomic transactions.

---

## 8. Frontend Technology Stack & Design System

### 8.1 Modern Technology Stack
- **Framework**: React 18+ with TypeScript 5.4.
- **Build Tool**: Vite 5 (ultra-fast HMR, optimized production chunks).
- **Styling**: Vanilla Tailwind CSS + Custom Design System Tokens (Zero unnecessary CSS frameworks).
- **State Management**:
  - **Server State**: TanStack Query v5 (React Query) for caching, background revalidation, and optimistic updates.
  - **Client State**: Zustand for lightweight UI state (active account, selected symbol, layout toggles).
- **Charting Engine**: **TradingView Lightweight Charts v4.1+** (Canvas-rendered, 60fps performance, customized for SMC Order Block bounding boxes, FVG highlight bands, and BOS lines).
- **Icons**: Lucide React.
- **Date Formatting**: `date-fns` (UTC-aware formatting).

### 8.2 Institutional Dark Design System (Design Tokens)
```css
/* Core Color Palette */
--bg-primary: #0B0F19;         /* Deep obsidian background */
--bg-surface: #111827;         /* Elevated card surface */
--bg-surface-elevated: #1F2937;/* Hover and modal backgrounds */
--border-subtle: #374151;      /* Crisp structural borders */
--text-primary: #F9FAFB;       /* High-contrast crisp text */
--text-secondary: #9CA3AF;     /* Muted financial labels */
--text-muted: #6B7280;         /* Timestamps and inactive text */

/* Semantic Financial Colors */
--bullish: #10B981;            /* Emerald Green (Buys, Longs, Profits) */
--bullish-subtle: #064E3B;     /* Bullish Order Block Shading */
--bearish: #F43F5E;            /* Rose Red (Sells, Shorts, Losses) */
--bearish-subtle: #881337;     /* Bearish Order Block Shading */
--accent-cyan: #06B6D4;        /* QuantEdge Brand & Technical Highlights */
--warning-amber: #F59E0B;      /* Macro Risk & High Volatility Alerts */
```

---

## 9. Comprehensive Page-by-Page Inventory & Specifications

### 9.1 Production User Trading App (`user-app/`)

```
┌───────────────────────────┬───────────────────────────────┬───────────────────────────────────────────┐
│ Page Route                │ Primary Purpose               │ Key Backend APIs Consumed                 │
├───────────────────────────┼───────────────────────────────┼───────────────────────────────────────────┤
│ /login                    │ Secure User Authentication    │ POST /api/v1/auth/login                   │
│ /signup                   │ New Account Registration      │ POST /api/v1/auth/signup                  │
│ /                         │ Executive Trading Dashboard   │ GET /api/v1/trade/status                  │
│                           │ & Portfolio Performance       │ GET /api/v1/account/summary               │
│                           │                               │ GET /api/v1/ai/enrichments                │
│ /terminal                 │ Advanced Trading Terminal     │ GET /api/v1/market/candles                │
│                           │ (TradingView Chart + SMC)     │ GET /api/v1/market/ticker/{symbol}        │
│                           │                               │ GET /api/v1/trade/orders                  │
│                           │                               │ GET /api/v1/trade/positions               │
│ /signals                  │ SMC Setups & AI Radar         │ GET /api/v1/trade/signals                 │
│                           │                               │ GET /api/v1/ai/enrichments/{setupId}      │
│ /intelligence             │ Live Market Intelligence      │ GET /api/v1/news                          │
│                           │ (News + Economic Calendar)    │ GET /api/v1/economic-events               │
│ /orders                   │ Complete Order Book & Fills   │ GET /api/v1/trade/orders                  │
│                           │                               │ GET /api/v1/trade/fills                   │
│ /positions                │ Positions & Real-Time P&L     │ GET /api/v1/trade/positions               │
│                           │                               │ GET /api/v1/trade/history                 │
│ /risk-algo                │ Algorithmic Trading Controls  │ POST /api/v1/trade/algo/toggle            │
│                           │ & Emergency Kill-Switch       │ POST /api/v1/trade/kill-switch            │
│                           │                               │ GET /api/v1/account/algo-config           │
│ /settings                 │ Exchange Keys & Security      │ POST /api/v1/account/connect              │
│                           │                               │ POST /api/v1/account/verify               │
│                           │                               │ GET /api/v1/account/status                │
└───────────────────────────┴───────────────────────────────┴───────────────────────────────────────────┘
```

### 9.2 Dedicated Developer App (`developer-app/`)

```
┌───────────────────────────┬───────────────────────────────┬───────────────────────────────────────────┐
│ Page Route                │ Primary Purpose               │ Key Backend APIs Consumed                 │
├───────────────────────────┼───────────────────────────────┼───────────────────────────────────────────┤
│ /developer                │ Developer Command Center      │ GET /api/v1/developer/status              │
│                           │ & Live Telemetry              │ GET /api/v1/developer/system/accounts     │
│ /developer/providers      │ External Feeds Health Monitor │ GET /api/v1/news/status                   │
│                           │ (Delta, CryptoCompare, Macro) │ GET /api/v1/economic-events/status        │
│                           │                               │ GET /api/v1/market/status                 │
│ /developer/diagnostics    │ API Latency & Health Checks   │ GET /api/v1/developer/diagnostics         │
│ /developer/logs           │ Sanitized Audit & Error Stream│ GET /api/v1/developer/logs                │
│ /developer/sandbox        │ Strategy Tick Simulator       │ GET /api/v1/developer/sandbox/info        │
│                           │ & Replay Sandbox              │ POST /api/v1/developer/sandbox/simulate-tick│
└───────────────────────────┴───────────────────────────────┴───────────────────────────────────────────┘
```

---

## 10. API Mapping & Gap Analysis

```
┌───────────────────────────────────────────────┬─────────────────────────┬─────────────────────────────┐
│ Frontend Feature                              │ API Endpoint            │ Backend Status              │
├───────────────────────────────────────────────┼─────────────────────────┼─────────────────────────────┤
│ User Auth & Profile                           │ /api/v1/auth/**         │ ✅ EXISTING API             │
│ Account Connect & AES-256 Keys                │ /api/v1/account/**      │ ✅ EXISTING API             │
│ TradingView Historical & Live OHLCV           │ /api/v1/market/candles  │ ✅ EXISTING API             │
│ Live Product Tickers & 24h Stats              │ /api/v1/market/ticker   │ ✅ EXISTING API             │
│ SMC Qualified Signals & Setups                │ /api/v1/trade/signals   │ ✅ EXISTING API             │
│ AI Confidence Scoring & Regime Analysis       │ /api/v1/ai/enrichments  │ ✅ EXISTING API             │
│ Categorized 7-Day Live Financial News         │ /api/v1/news            │ ✅ EXISTING API             │
│ 15-Day Rolling Global Macro Economic Calendar │ /api/v1/economic-events │ ✅ EXISTING API             │
│ In-App Alert Notifications & Read State       │ /api/v1/notifications   │ ✅ EXISTING API             │
│ Orders, Positions & Execution Fills           │ /api/v1/trade/**        │ ✅ EXISTING API             │
│ Algo Enable/Disable & Kill Switch Controls    │ /api/v1/trade/**        │ ✅ EXISTING API             │
│ Developer System Health & Diagnostics         │ /api/v1/developer/**    │ ✅ EXISTING API             │
│ External Provider Health Status               │ /api/v1/*/status        │ ✅ EXISTING API             │
│ Real-Time WebSocket Candlestick Stream        │ /ws/market/candles      │ ⚠️ OPTIONAL FUTURE (REST OK) │
└───────────────────────────────────────────────┴─────────────────────────┴─────────────────────────────┘
```

---

## 11. Safe Non-Destructive Migration Strategy

```
Phase 9.1: Project Setup & Core Design Tokens
    ├── Create user-app/ with Vite, React 18, TypeScript, TailwindCSS
    ├── Create developer-app/ with independent build configuration
    └── Verify zero impact on existing frontend/ and backend tests

Phase 9.2: User App Development (Modules 1–4)
    ├── Auth & Account Management
    ├── TradingView Charting Engine with SMC Overlay Renderer
    ├── Signal Radar & AI Intelligence Panel
    └── Live Financial News & Macroeconomic Calendar Feeds

Phase 9.3: User App Development (Modules 5–7)
    ├── Live Order Book, Position Manager & Execution Fills
    ├── Risk Configuration, Algo Toggle & Emergency Kill-Switch
    └── Settings & AES-256 Exchange Key Management

Phase 9.4: Developer App Implementation
    ├── Multi-Tenant Account Telemetry
    ├── Provider Sync & Latency Diagnostics
    └── Strategy Sandbox Lab & Redacted Log Stream

Phase 9.5: Verification, Audit & Old Frontend Deprecation
    ├── End-to-End browser verification against live backend
    ├── Automated regression & security audit
    ├── Formal User Approval to deprecate legacy frontend/
    └── Final repository cleanup & Git synchronization
```

---

## 12. Conclusion & Request for User Approval

The backend, database, and deterministic engine infrastructure are completely hardened and ready. The proposed architecture separates user trading operations from developer observability, eliminates code bloat, enforces strict tenant isolation, and elevates QuantEdge AI into a premier algorithmic trading terminal.

**Awaiting your review and explicit approval to proceed with Phase 9 implementation.**
