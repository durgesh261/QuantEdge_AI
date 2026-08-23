# QuantEdge AI — Phase 9 Deep System Audit
## Complete End-to-End Architectural Inspection & Technical Audit

---

## 1. Executive Summary

This document presents a comprehensive, read-only architectural audit of the complete QuantEdge AI codebase across all layers:
- **Backend**: Spring Boot 3.4.1 (Java 21), Spring Data JPA, Flyway, Spring Security with JWT.
- **Engine**: Deterministic Python SMC Engine, 1H canonical stream, Rule-Based AI Intelligence Enricher.
- **Database**: PostgreSQL 16 with Flyway migrations V1 through V6.
- **Legacy Frontend**: React 18, Vite 5, TailwindCSS (preserved temporarily as a reference).
- **External Feeds**: Delta Exchange India (`DELTAIN`), CryptoCompare News API, ForexFactory / Faireconomy Macroeconomic Calendar API.

---

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

---

## 3. Real Order Authority Verification
- **Sole Location**: `backend/src/main/java/com/quantedge/trading/service/OrderExecutionService.java` at line 312.
- **Search Verification**: A complete grep of the entire repository confirms that `POST /v2/orders` is called **exclusively** within `OrderExecutionService.java`.
- **Zero Execution in Other Modules**:
  - `MarketDataController` & `DeltaMarketDataClient`: Strictly read-only public market data.
  - `NewsController` & `ExternalFinancialNewsProvider`: Strictly read-only public news ingestion.
  - `EconomicCalendarController` & `ExternalEconomicCalendarProvider`: Strictly read-only economic event synchronization.
  - `NotificationController` & `NotificationService`: In-app alerts only.
  - `AiIntelligenceController` & `AiEnrichmentService`: Analytical scoring only.

---

## 4. Frozen SMC Core Verification
The following 3 algorithmic core files are strictly frozen:
1. `engine/src/quantedge/smc/structure.py`
2. `engine/src/quantedge/smc/order_blocks.py`
3. `engine/src/quantedge/smc/volatility.py`

**Diff against `origin/main`**:
```powershell
git diff origin/main -- engine/src/quantedge/smc/structure.py engine/src/quantedge/smc/order_blocks.py engine/src/quantedge/smc/volatility.py
# Result: ZERO DIFF
```

---

## 5. Summary of Invariant Audit
| System Area | Audit Result | Status |
| :--- | :--- | :--- |
| **SMC Core Files** | 0 changes against origin/main | **STRICTLY FROZEN** |
| **Real-Order Authority** | Exclusive to `OrderExecutionService.java:312` | **VERIFIED** |
| **Database Migrations** | V1 → V6 clean Flyway sequence | **VERIFIED** |
| **Root `database/` Folder** | Not required; all migrations in `backend/` | **VERIFIED** |
| **Tenant Isolation** | All queries enforce `user_id` ownership | **VERIFIED** |
| **Credential Security** | AES-256 encryption; 0 secret leaks in DTOs | **VERIFIED** |
| **External Providers** | Live REST with safe fallback | **VERIFIED** |
