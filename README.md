# QuantEdge AI

<div align="center">

**Institutional-Grade Distributed Algorithmic Trading Platform for Cryptocurrency Perpetuals**

[![Tests](https://img.shields.io/badge/Test_Suite-845%2B_Passing-success?style=for-the-badge&logo=pytest&logoColor=white)](engine/tests)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Java](https://img.shields.io/badge/Java-21_LTS-orange?style=for-the-badge&logo=openjdk&logoColor=white)](https://openjdk.org)
[![Spring Boot](https://img.shields.io/badge/Spring_Boot-3.2-brightgreen?style=for-the-badge&logo=springboot&logoColor=white)](https://spring.io)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Docker](https://img.shields.io/badge/Docker-Compose_Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](docker-compose.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

</div>

---

## 1. Executive Summary

**QuantEdge AI** is a production-grade, distributed quantitative trading system engineered for high-reliability algorithmic execution of cryptocurrency perpetual futures (BTCUSD, ETHUSD, SOLUSD, XRPUSD) on **Delta Exchange India**. 

The system implements a proven, causal **Smart Money Concepts (SMC)** market-structure strategy, backed by a microservices architecture that decouples low-latency WebSocket market-data ingestion from an authoritative, multi-layered risk validation gateway. It eliminates discretionary bias through deterministic geometry, strict causality (zero lookahead bias), atomic restart recovery, and mathematical position sizing.

---

## 2. High-Level System Architecture

```
                                  [ Delta Exchange India ]
                                     ▲                │
                   Private REST API  │                │ Public WebSocket Feed
                   (Orders, Fills)   │                │ (1s Ticks, 1h Candles)
                                     │                ▼
                             ┌─────────────────────────────────┐
                             │    Python Execution Engine      │
                             │        (Port: 8000)             │
                             │  • Two-Clock Market Pipeline    │
                             │  • Causal SMC Structure Scanner │
                             │  • Deterministic State Machine  │
                             │  • Stale Feed Watchdog + Backfill│
                             └────────────────┬────────────────┘
                                              │
                      Authoritative REST IPC  │ Order Execution Requests
                      (HMAC / Key Authorized) │ & Risk Handshake
                                              ▼
                             ┌─────────────────────────────────┐
                             │    Java 21 Spring Boot Gateway  │
                             │        (Port: 8080)             │
                             │  • Pre-Trade Validation Engine  │
                             │  • 1x–100x Leverage Band Policy │
                             │  • Single-Trade Portfolio Lock  │
                             │  • AES-256 Credential Vault     │
                             └────────────────┬────────────────┘
                                              │
                         JDBC Connection Pool │ PostgreSQL 16
                         (Flyway Migrations)  ▼
                             ┌─────────────────────────────────┐
                             │    PostgreSQL Database (5432)   │
                             │  • Trade & Position Ledger      │
                             │  • Encrypted User Credentials   │
                             │  • Atomic Recovery Snapshots    │
                             └─────────────────────────────────┘
                                              ▲
                                              │ REST / WebSocket
                             ┌────────────────┴────────────────┐
                             │                                 │
                             ▼                                 ▼
              ┌─────────────────────────────┐   ┌─────────────────────────────┐
              │   Trader Web Terminal       │   │  Diagnostic Admin Console   │
              │ React 18 + Vite (Port 3100) │   │ React 18 + Vite (Port 3101) │
              │ • Real-Time PnL & Charts    │   │ • Health & Latency Telemetry│
              │ • Trade Lifecycle Control   │   │ • Risk Overrides & Auditing │
              └─────────────────────────────┘   └─────────────────────────────┘
```

---

## 3. Core Architectural Highlights

### ⚡ Two-Clock Market Data Processing Model
Financial time-series demand separation between macro market context and micro execution pricing:
1. **1-Hour Structure Clock**: Ingests finalized hourly candlestick frames to evaluate swing highs, swing lows, and Order Block (OB) formation. Incomplete forming candles are strictly prevented from corrupting structural state.
2. **Sub-Minute Live Execution Clock**: Listens to streaming live tick updates over WebSockets to evaluate sub-minute price touches against active limit orders, maintaining sub-millisecond execution reaction times.

### 🛡️ Two-Tier Authoritative Risk Gate
Trading execution is governed by a fail-closed architecture where risk enforcement is physically separated from strategy generation:
- **Python Engine**: Proposes target orders and calculates theoretical sizing.
- **Java Spring Boot Gateway (Authoritative)**: Intercepts all order intents prior to exchange dispatch. Enforces strict invariants:
  - **Dynamic Leverage Band (1x–100x)**: Rejects any order violating the asset-specific leverage constraints.
  - **Single-Trade Portfolio Lock**: Ensures only **one active trade** exists across the entire cryptocurrency portfolio at any given time, preventing margin cascades.
  - **Symbol Policy Contracts**: Rejects malformed symbols or unauthorized trading pairs.

### 🔄 Fault-Tolerant Restart Recovery & Idempotency
- **Atomic State Persistence**: Structure state and order lifecycles are persisted atomically with write-ahead verification.
- **Startup Reconciliation Gate**: On restart, the engine queries exchange REST endpoints, compares local state against live exchange positions, and resolves discrepancies before arming the trading loop.
- **Deterministic Order Identity Triples**: Every entry, stop-loss, and take-profit order is tagged with a deterministic hash:
  ```
  ClientOrderID = Hash(setup_id, role, account_id)
  ```
  This guarantees idempotency across network reconnects and eliminates duplicate order executions.

---

## 4. Algorithmic Strategy: Manual TradingView SMC

The active trading model replicates verified institutional **Smart Money Concepts (SMC)** with candle-by-candle mathematical precision:

```
  BEARISH SETUP (SHORT)                       BULLISH SETUP (LONG)
  
       [origin.HIGH]                               [ob_top: Proximal] (origin.HIGH)
   ─── [ob_top: Distal / Stop Loss] (origin.CLOSE)  ───────────────────────────────
         ▲                                                │
         │  OB Width                                      ▼  OB Width (25% Depth Entry)
         │                                          ───── [Entry Price]
   ─── [ob_bottom: Proximal] (origin.LOW)                 │
         │                                                ▲
         ▼  (25% Depth Entry)                             │
   ───── [Entry Price]                      ─── [ob_bottom: Distal / Stop Loss] (origin.CLOSE)
         │                                         [origin.LOW]
         ▼  Take Profit (+0.60% fixed)                    ▼  Take Profit (+0.60% fixed)
```

### Mathematical Formulation
* **Order Block Identification**: Triggered upon a verified **Break of Structure (BOS)** across a sliding lookback window of `N = 10` bars.
* **Direction-Specific Distal Boundary**: 
  - For Short: `ob_top = origin.CLOSE` *(Wick extreme is ignored)*
  - For Long: `ob_bottom = origin.CLOSE`
* **Entry Level (25% Depth into Order Block)**:
  - Short Entry: `ob_bottom + 0.25 * (ob_top - ob_bottom)`
  - Long Entry: `ob_top - 0.25 * (ob_top - ob_bottom)`
* **Take Profit (TP)**: Fixed `+0.60%` price movement from entry.
* **Pre-Entry Invalidation**: Any wick touch beyond the distal boundary prior to order fill invalidates the setup immediately.
* **Admission Timing**: New Order Blocks are admitted into the active monitoring pool at **Break + 1** (first bar open after BOS confirmation), eliminating lookahead bias.

---

## 5. Technology Stack

| Component | Technology | Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Execution Engine** | Python | 3.11+ | Real-time WebSocket ingestion, SMC scanner, state lifecycle |
| **Risk & Gateway** | Java (Spring Boot) | Java 21 / Spring 3.2 | Authoritative order validation, trade persistence, JWT auth |
| **Database** | PostgreSQL | 16-alpine | ACID trade ledger, encrypted credentials, Flyway migrations |
| **Trader UI** | React / TypeScript | React 18 / Vite | Live trading terminal, TradingView charts, position management |
| **Operator UI** | React / TypeScript | React 18 / Vite | Health telemetry, latency monitors, system diagnostics |
| **Containerization**| Docker Compose | v2+ | Unified local & cloud container orchestration |
| **Testing** | PyTest / JUnit 5 | Latest | 845+ unit, integration, parity, and golden acceptance tests |

---

## 6. Project Structure

```
QuantEdge AI/
├── backend/                   # Java 21 Spring Boot Enterprise Gateway
│   ├── src/main/java/         # Controllers, Services, Security, JPA Repositories
│   ├── src/main/resources/    # Application configs, Flyway database migrations
│   └── pom.xml                # Maven configuration with Surefire & test dependencies
├── engine/                    # Python Asynchronous Market & Execution Engine
│   ├── src/quantedge/         # Core application package
│   │   ├── execution/         # Order lifecycle, Delta client, admission gates
│   │   ├── market_data/       # WebSocket feeds, live aggregator, backfill logic
│   │   ├── runtime/           # Startup gate, restart recovery, credential provider
│   │   └── strategy/          # Manual SMC scanner, geometry, sizing, backtest
│   ├── tests/                 # 845+ automated tests (unit, audit, parity, acceptance)
│   └── pyproject.toml         # Python build specification
├── user-app/                  # React 18 Trader Dashboard (Port 3100)
├── developer-app/             # React 18 Operator & Diagnostic Console (Port 3101)
├── docker/                    # Specialized Dockerfiles (Engine, Backend, Frontends)
├── docs/                      # Authoritative specifications and architecture manuals
├── docker-compose.yml         # Unified orchestration manifest
└── LICENSE                    # MIT License
```

---

## 7. Quick Start & Local Execution

### Prerequisites
- **Docker & Docker Compose** (recommended)
- *Or bare-metal*: Java 21+, Python 3.11+, Node.js 20+, PostgreSQL 16+

### Option A: Running with Docker Compose (Recommended)

1. **Clone the repository and initialize environment**:
   ```bash
   git clone https://github.com/durgesh261/QuantEdge_AI.git
   cd QuantEdge_AI
   cp .env.example .env
   ```

2. **Configure your `.env`**:
   Populate credentials, secrets, and database settings as outlined in `.env.example`.

3. **Start all services**:
   ```bash
   docker compose up -d
   ```
   * **PostgreSQL**: `localhost:5432`
   * **Spring Boot API**: `http://localhost:8080`
   * **Python Engine**: `http://localhost:8000`
   * **Trader Terminal**: `http://localhost:3100`
   * **Operator Console**: `http://localhost:3101`

---

### Option B: Running Individual Services Locally

```bash
# 1. Start the PostgreSQL container
docker compose up -d postgres

# 2. Launch the Java Backend Gateway
cd backend
./mvnw spring-boot:run

# 3. Launch the Python Engine
cd ../engine
pip install -e .
python -m quantedge

# 4. Launch Frontends
cd ../user-app && npm ci && npm run dev
cd ../developer-app && npm ci && npm run dev
```

---

## 8. Verification & Test Suite

The system includes an exhaustive test harness covering structural invariants, race conditions, leverage policies, and live order reconciliation.

```bash
# Run the complete Python test suite (845 tests, 100% passing)
python -m pytest engine/tests -v

# Run the Golden BTC Reference Acceptance Test
python -m pytest engine/tests/test_manual_smc_btc_acceptance.py -v

# Run Backend Spring Boot Integration Tests
cd backend && ./mvnw test
```

---

## 9. Authoritative Specifications

Comprehensive architectural blueprints and mathematical specifications are documented in [`docs/`](docs/):
* [System Architecture Blueprint](docs/ARCHITECTURE.md)
* [Manual SMC Strategy Specification](docs/MANUAL_SMC_STRATEGY.md)
* [Backend REST API Specification](docs/API_SPECIFICATION.md)
* [Database Schema & Entity Models](docs/DATABASE_SPECIFICATION.md)
* [Pre-Trade Risk & Sizing Specification](docs/RISK_SPECIFICATION.md)
* [Security & Credential Vault Standards](docs/SECURITY.md)

---

## License

This project is licensed under the [MIT License](LICENSE).
