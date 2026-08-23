# QuantEdge AI — Phase 9 Final Target Architecture
## Comprehensive System Topology, Dual-Application Structure & Design Freeze

---

## 1. Unified Repository & Application Structure

```
QuantEdge-AI/
├── backend/                                # Spring Boot 3.4.1 Backend
│   ├── src/main/java/com/quantedge/
│   │   ├── auth/                           # Authentication & User Management
│   │   ├── account/                        # Multi-Account & AES-256 Key Encryption
│   │   ├── trading/                        # Orders, Positions, Fills & SOLE ORDER AUTHORITY
│   │   ├── market/                         # Delta Exchange India Market Data & Candles
│   │   ├── news/                           # 7-Day Live Financial & Crypto News
│   │   ├── economic/                       # 15-Day Rolling Macroeconomic Calendar
│   │   ├── notification/                   # In-App User Alerts & Deduplication
│   │   ├── ai/                             # Signal Enrichment & Regime Intelligence
│   │   ├── developer/                      # Telemetry, Latency Probing & Sandbox Lab
│   │   └── engine/                         # Python Engine State Bridge
│   └── src/main/resources/db/migration/    # Flyway Migrations (V1 to V6)
│
├── engine/                                 # Deterministic Python SMC & AI Engine
│   └── src/quantedge/
│       ├── smc/                            # [STRICTLY FROZEN] structure, order_blocks, volatility
│       ├── strategy/                       # Signal Qualification & RR Filter
│       ├── ai/                             # AI Rule-Based Signal Enricher
│       └── execution/                      # Multi-User Orchestrator & Capital Allocator
│
├── user-app/                               # [PHASE 9 TARGET] Production User Trading Web App
│   ├── package.json                        # Independent dependencies (Vite, React 18, TV Charts)
│   ├── vite.config.ts                      # Port 3000 build configuration
│   └── src/
│       ├── components/                     # Header, Sidebar, TradingViewCanvas, Modals
│       ├── features/
│       │   ├── auth/                       # Login, Signup
│       │   ├── dashboard/                  # Executive Portfolio Dashboard
│       │   ├── terminal/                   # Advanced Trading Terminal (Chart + SMC + Ticket)
│       │   ├── signals/                    # Qualified Setups & AI Radar
│       │   ├── intelligence/               # Financial News & 15d Macro Calendar
│       │   ├── orders/                     # Order Book & Execution Fills
│       │   ├── positions/                  # Active Positions & Realized P&L Ledger
│       │   ├── risk/                       # Risk Settings, Algo Toggle & Kill-Switch
│       │   └── settings/                   # Delta Key Configuration & Security
│       ├── services/                       # Type-Safe REST API Clients
│       ├── stores/                         # Zustand Client Stores + TanStack Query Cache
│       └── styles/                         # Institutional Dark Theme Design Tokens
│
├── developer-app/                          # [PHASE 9 TARGET] Standalone Developer Console
│   ├── package.json                        # Independent dependencies (Vite, React 18, Lucide)
│   ├── vite.config.ts                      # Port 3001 build configuration
│   └── src/
│       ├── components/                     # DeveloperLayout, MetricCards, StatusBadges
│       ├── features/
│       │   ├── command-center/             # JVM, PostgreSQL, Engine Vitals
│       │   ├── accounts/                   # Multi-Tenant Health & Active Lock Inspector
│       │   ├── providers/                  # Delta, CryptoCompare, Faireconomy Monitors
│       │   ├── diagnostics/                # Roundtrip Latency & Thread Pool Diagnostics
│       │   ├── logs/                       # Redacted Real-Time Log Viewer
│       │   └── sandbox/                    # Strategy Tick Simulator Lab
│       ├── services/                       # Developer API Client
│       └── stores/                         # Developer State Stores
│
├── frontend/                               # [LEGACY REFERENCE — UNTOUCHED UNTIL APPROVAL]
└── docker-compose.yml                      # Orchestrates Postgres, Redis, Backend, Engine, Apps
```

---

## 2. Definitive Feature Prioritization Matrix

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

## 3. Architecture Sign-Off & Verification

- **Current Repository State**: Clean, no code modified, zero deletions.
- **SMC Frozen Core**: Verified **ZERO DIFF** on `structure.py`, `order_blocks.py`, `volatility.py`.
- **Sole Real-Order Authority**: Verified exclusively in `OrderExecutionService.java:312`.
- **Database Schema**: Single authoritative PostgreSQL Flyway migration chain (V1 to V6). Zero database duplication.
- **Legacy Frontend**: Untouched in `frontend/`.
