# QuantEdge AI — Dedicated Developer Observability App Architecture
## Standalone System Observability, Diagnostic Telemetry & Sandbox Lab Specification

---

## 1. Executive Overview

The **QuantEdge Developer Observability App** (`developer-app/`) is a dedicated administrative and engineering console designed to provide quant developers, platform engineers, and system administrators with end-to-end operational visibility into the QuantEdge AI ecosystem.

### Core Architectural Invariants
1. **Single Backend & Database**: Operates against the same authoritative Spring Boot backend (`http://localhost:8080`) and PostgreSQL 16 database. **Zero database duplication.**
2. **Zero Direct Database Connections**: All telemetry and diagnostics are fetched strictly via authenticated backend REST endpoints.
3. **Strict RBAC Enforcement**: Server-side `@PreAuthorize("hasAnyRole('DEVELOPER', 'ADMIN')")` protects all `/api/v1/developer/**` endpoints. Regular trading users (`ROLE_USER`) receive HTTP 403 Forbidden.
4. **Data Redaction**: Sensitive information (API secrets, private encryption keys, PII) is automatically sanitized and masked before transmission.
5. **Zero Impact on Trading Engine**: Diagnostic and sandbox operations run in isolated memory spaces without interfering with the live 1H SMC execution pipeline.

---

## 2. Directory Structure & Build Configuration

The Developer App is housed in `developer-app/` within the main repository, providing a clean separation from user trading code while sharing TypeScript interface contracts:

```
developer-app/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── index.css
│   ├── assets/
│   ├── components/
│   │   ├── DeveloperLayout.tsx
│   │   ├── DeveloperHeader.tsx
│   │   ├── DeveloperSidebar.tsx
│   │   ├── MetricCard.tsx
│   │   ├── StatusBadge.tsx
│   │   └── LogViewerTerminal.tsx
│   ├── features/
│   │   ├── dashboard/
│   │   │   ├── DeveloperDashboard.tsx
│   │   │   └── SystemMetricsGrid.tsx
│   │   ├── accounts/
│   │   │   ├── AccountsInspector.tsx
│   │   │   └── AccountHealthDetailModal.tsx
│   │   ├── providers/
│   │   │   ├── ProviderHealthMonitor.tsx
│   │   │   ├── DeltaExchangeHealthCard.tsx
│   │   │   ├── NewsProviderHealthCard.tsx
│   │   │   └── EconomicCalendarHealthCard.tsx
│   │   ├── diagnostics/
│   │   │   ├── ApiDiagnostics.tsx
│   │   │   └── LatencyBenchmarkTable.tsx
│   │   ├── logs/
│   │   │   ├── RedactedLogsViewer.tsx
│   │   │   └── LogFilterToolbar.tsx
│   │   └── sandbox/
│   │       ├── StrategySandboxLab.tsx
│   │       ├── TickSimulatorForm.tsx
│   │       └── SetupReplayVisualizer.tsx
│   ├── services/
│   │   ├── devApiClient.ts
│   │   └── developerService.ts
│   ├── stores/
│   │   └── developerStore.ts
│   └── types/
│       └── developer.ts
```

---

## 3. Core Feature Modules & UI Specifications

### 3.1 Developer Command Center (`/developer`)
- **Purpose**: High-level telemetry dashboard displaying real-time platform vitals.
- **Key Vitals Displayed**:
  - **JVM Health**: Uptime, Memory (Heap Used / Max), Thread counts.
  - **Engine Connectivity**: Python SMC Engine REST bridge status (`/api/engine`).
  - **PostgreSQL Pool**: Active connections, idle connections, Flyway migration version (`V6`).
  - **Active Algo Accounts**: Total registered accounts vs. actively running algo accounts.
  - **Emergency State**: Global kill-switch status and recent risk alerts.
- **Backend API**: `GET /api/v1/developer/status`

### 3.2 Multi-Tenant Account & Engine State Inspector (`/developer/accounts`)
- **Purpose**: Deep operational inspection of all trading accounts across the platform.
- **Features**:
  - Account list with search, filter by active/inactive, exchange connection status.
  - Active trade lock inspector (`active_trade_locks`): Shows locked `setupId`, acquisition timestamp, and lock duration.
  - Open orders and position count per account.
  - Last sync timestamp and recent error messages.
- **Backend API**: `GET /api/v1/developer/system/accounts`

### 3.3 External Provider Health Monitor (`/developer/providers`)
- **Purpose**: Real-time observability for all external data pipelines.
- **Feeds Monitored**:
  1. **Delta Exchange India Market Feed**: REST latency, candle ingestion health, ticker stream.
  2. **CryptoCompare Financial News**: Last sync time, total ingested articles, error counts, SHA-256 deduplication efficiency.
  3. **ForexFactory / Faireconomy Macroeconomic Calendar**: Sync frequency, total synchronized events, 15-day rolling window bounds.
- **Backend APIs**:
  - `GET /api/v1/news/status`
  - `GET /api/v1/economic-events/status`
  - `GET /api/v1/market/status`

### 3.4 API Diagnostics & Latency Benchmark (`/developer/diagnostics`)
- **Purpose**: Diagnostic probing of internal service-to-service communication.
- **Features**:
  - Live roundtrip latency metrics (Backend ↔ PostgreSQL, Backend ↔ Python Engine, Backend ↔ Delta API).
  - Rate limit headroom counters.
- **Backend API**: `GET /api/v1/developer/diagnostics`

### 3.5 Sanitized Audit & Error Stream (`/developer/logs`)
- **Purpose**: Live terminal-style log viewer for developer debugging.
- **Security Features**:
  - Automatically redacts passwords, tokens, API keys (`api_key=***`).
  - Color-coded severity (`INFO`, `WARN`, `ERROR`, `SECURITY`).
  - Instant text search and log level filter.
- **Backend API**: `GET /api/v1/developer/logs`

### 3.6 Interactive Strategy Sandbox Lab (`/developer/sandbox`)
- **Purpose**: Dry-run environment for quant engineers to simulate market price movements and observe strategy qualification without executing live orders.
- **Features**:
  - Manual price tick simulator (`POST /api/v1/developer/sandbox/simulate-tick`).
  - Visual setup evaluation inspector showing SMC qualification criteria (Order Block alignment, RR > 2.0, ATR filter).
- **Backend API**:
  - `GET /api/v1/developer/sandbox/info`
  - `POST /api/v1/developer/sandbox/simulate-tick`

---

## 4. API Gap Analysis for Developer App

```
┌───────────────────────────────────────────────────┬───────────────────────────────────────────┬─────────────────────┐
│ Developer App Feature                             │ Backend API Endpoint                      │ Current Status      │
├───────────────────────────────────────────────────┼───────────────────────────────────────────┼─────────────────────┤
│ System Telemetry & JVM Status                     │ GET /api/v1/developer/status              │ ✅ EXISTING API     │
│ API Latency Diagnostics                           │ GET /api/v1/developer/diagnostics         │ ✅ EXISTING API     │
│ Sanitized Real-Time Logs                          │ GET /api/v1/developer/logs                │ ✅ EXISTING API     │
│ Sandbox Info & Simulated Tick                     │ POST /api/v1/developer/sandbox/simulate-tick│ ✅ EXISTING API   │
│ Account Health Summaries                          │ GET /api/v1/developer/system/accounts     │ ✅ EXISTING API     │
│ News Provider Status                              │ GET /api/v1/news/status                   │ ✅ EXISTING API     │
│ Economic Provider Status                          │ GET /api/v1/economic-events/status        │ ✅ EXISTING API     │
│ Market Data Status                                │ GET /api/v1/market/status                 │ ✅ EXISTING API     │
│ Deep Lock Purge / Reset Admin Action              │ POST /api/v1/developer/locks/{id}/release │ ⚠️ NEW API (PHASE 9)│
│ Flyway Schema Migration History Details           │ GET /api/v1/developer/db/migrations       │ ⚠️ NEW API (PHASE 9)│
└───────────────────────────────────────────────────┴───────────────────────────────────────────┴─────────────────────┘
```

---

## 5. Security & Isolation Verification

- **Zero Secret Leakage**: The Developer App never receives raw Delta API secrets or user passwords.
- **Strict Role Enforcement**: If a user without `ROLE_DEVELOPER` or `ROLE_ADMIN` accesses `/developer/*`, the React router redirects to `/` and the backend returns HTTP 403 Forbidden.
- **Independent Bundle**: Building `developer-app/` produces a separate production bundle, ensuring regular users do not download developer diagnostic scripts.
