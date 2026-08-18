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

### React Frontend (`/frontend`)
- **Only** user interface and presentation
- Communicates exclusively with Spring Boot REST API
- No business logic, no trading logic, no market data processing
- State management for UI only (not authoritative)

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
- LuxAlgo SMC implementation (canonical reference)
- Market structure analysis (swing/internal, BOS/CHOCH)
- Order Block detection & lifecycle
- Liquidity & Equal Levels detection
- Fair Value Gap detection
- Strategy candidate generation
- Confidence scoring (9-factor model)
- Deterministic backtesting
- AI/ML research framework

### PostgreSQL Database (`/database`)
- **Authoritative** persistent storage
- Multi-tenant user data isolation
- ACID compliance for financial data
- Flyway migrations for schema versioning
- Row-level security for account isolation

### Delta Exchange
- External execution venue
- Credentials stored encrypted in Spring Boot
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

### Spring Boot ↔ PostgreSQL
- JPA/Hibernate ORM
- Flyway migrations
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
- **Delta credentials encrypted** at rest
- **JWT tokens** - short-lived access, refresh rotation
- **HttpOnly cookies** - no token exposure to JS
- **Row-level security** - database enforces isolation
- **Spring Boot enforces risk** - Python cannot bypass

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
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   React     │────▶│ Spring Boot │────▶│ PostgreSQL  │
│  (Nginx)    │     │  (Java 21)  │     │   (16+)     │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    │   Python    │
                    │  Engine     │
                    │  (3.11+)    │
                    └─────────────┘
```

Local development: `docker-compose up -d` (PostgreSQL + Redis)
Production: Kubernetes/GKE with Cloud SQL