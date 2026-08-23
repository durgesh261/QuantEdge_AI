# QuantEdge AI V2 Architecture

## Overview

QuantEdge AI V2 is a clean architecture implementation separating concerns across four primary components:

```
┌─────────────────────────────────────────────────────────────────┐
│                        QuantEdge AI V2                          │
├─────────────┬─────────────┬─────────────┬───────────────────────┤
│   React     │ Spring Boot │   Python    │      PostgreSQL       │
│  Frontend   │  Backend    │   Engine    │      Database         │
└──────┬──────┴──────┬───────┴──────┬──────┴───────────────────────┘
       │             │              │
       ▼             ▼              ▼
   UI Only      App/Risk/      Market Intel
   (Presentation) Execution      & Strategy
                          Authority
```

## Component Responsibilities

### User Application (`/user-app`)
- **Only** user interface and presentation for traders
- React + TypeScript + Vite on port 3100
- Communicates exclusively with Spring Boot REST API via /api proxy
- No business logic, no trading logic, no market data processing
- State management for UI only (not authoritative)

### Developer Application (`/developer-app`)
- Developer/Operator console for system diagnostics
- React + TypeScript + Vite on port 3101
- RBAC-restricted: requires ROLE_DEVELOPER or ROLE_ADMIN
- Engine sandbox, log viewer, execution monitor, market diagnostics

### Spring Boot Backend (`/backend`)
- **Authoritative** application layer
- Authentication & Authorization (JWT-based)
- User management & settings
- Trading account management
- Delta Exchange connection management (encrypted credentials)
- Order & Position management
- Risk validation & enforcement
- Paper & Live trading execution
- Journal & Analytics
- Audit logging
- Multi-user isolation enforcement

### Python Engine (`/engine`)
- Market intelligence & strategy research
- Real & historical market data ingestion
- Candle processing & OHLCV management
- **LuxAlgo SMC implementation (canonical reference)**
  - Stateful leg-based structure detection (internal length=5, swing length=50)
  - ATR(200) volatility parsing with LuxAlgo inversion logic
  - BOS/CHOCH detection with proper confirmation timing
  - Order Block detection via LuxAlgo slice semantics (inclusive start, exclusive end)
  - OB lifecycle: FRESH -> TOUCHED -> USED / INVALIDATED
- Liquidity & Equal Levels detection
- Fair Value Gap detection
- Strategy candidate generation
- **Confidence scoring (8-factor model)**
- Deterministic backtesting
- AI/ML research framework
- **NO Delta credentials** - receives market data only

### PostgreSQL Database (`/database`)
- **Authoritative** persistent storage
- Multi-tenant user data isolation
- ACID compliance for financial data
- Flyway migrations for schema versioning (ONLY mechanism)
- Application-level ownership enforcement (RLS planned for hardening)

### Delta Exchange
- External execution venue
- Credentials stored encrypted in Spring Boot ONLY
- No direct access from React or Python Engine

## Communication Patterns

### React ↔ Spring Boot
- REST API with OpenAPI contract
- JWT authentication (HttpOnly cookies)
- WebSocket for real-time updates (orders, positions, P&L)

### Python Engine ↔ Spring Boot
- Strategy candidate API (HTTP/JSON)
- Spring Boot validates & executes
- Python Engine never writes user data or submits orders
- **Python Engine has NO Delta credentials**

### Spring Boot ↔ PostgreSQL
- JPA/Hibernate ORM
- Flyway migrations (ONLY schema mechanism)
- Connection pooling (HikariCP)

## Data Flow

```
Market Data (Delta) → Python Engine → SMC Analysis → Strategy Candidates
                                                          ↓
                                            Spring Boot Risk Validation
                                                          ↓
                                                Execution (Paper/Live)
                                                          ↓
                                                PostgreSQL Persistence
                                                          ↓
                                                React UI Updates
```

## Security Model

- **No global secrets** - all per-user/account
- **Delta credentials encrypted** at rest (Spring Boot only)
- **JWT tokens** - short-lived access, refresh rotation
- **HttpOnly cookies** - no token exposure to JS
- **Application-level ownership enforcement** - database RLS planned
- **Spring Boot enforces risk** - Python cannot bypass
- **Python Engine receives NO Delta credentials**

## Multi-User Isolation

Every user has completely independent:
- Authentication & sessions
- Trading accounts & settings
- Delta API credentials (encrypted)
- Orders, positions, balances
- P&L, journal, analytics
- Paper/Live trading state
- ALGO ON/OFF, Delta ON/OFF

No shared/trading state across users.

## Deployment

```
┌─────────────────┐     ┌─────────────┐     ┌─────────────┐
│  User App       │────▶│ Spring Boot │────▶│ PostgreSQL  │
│  (Nginx :3100)  │     │  (Java 21)  │     │   (16+)     │
└─────────────────┘     └──────┬──────┘     └─────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
       │   Python    │  │  Developer  │  │    Redis    │
       │  Engine     │  │   App       │  │  (optional) │
       │  (:8000)    │  │  (:3101)    │  │  (:6379)    │
       └─────────────┘  └─────────────┘  └─────────────┘
```

Local development: `docker-compose up -d`
- PostgreSQL starts empty (port 5432)
- Spring Boot backend starts (port 8080)
- Flyway applies migrations exactly once
- Python Engine starts on port 8000 (no Delta credentials)
- User App starts on port 3100
- Developer App starts on port 3101
- Redis starts on port 6379 (if enabled)

Ports:
- 3100 — User App (React)
- 3101 — Developer App (React)
- 8000 — Python Engine (FastAPI)
- 8080 — Spring Boot Backend
- 5432 — PostgreSQL
- 6379 — Redis (optional)

Production: Kubernetes/GKE with Cloud SQL