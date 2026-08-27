# QuantEdge AI

**Automated Cryptocurrency Trading Platform for Delta Exchange India**

QuantEdge AI is an automated, institutional-grade trading system designed to trade cryptocurrency perpetuals on Delta Exchange India (BTCUSD, ETHUSD, SOLUSD, XRPUSD) using the verified **Manual TradingView SMC Strategy**.

---

## 1. System Architecture

```
QuantEdge AI
├── backend/           # Java 21 + Spring Boot 3.2 REST API & Gateway (Port 8080)
├── engine/            # Python 3.11+ Market Data & Trading Engine (Port 8000)
├── user-app/          # React 18 + TypeScript + Vite Trader Terminal (Port 3100)
├── developer-app/     # React 18 + TypeScript + Vite Diagnostic Console (Port 3101)
├── data/canonical/    # Canonical Delta Exchange India historical datasets
├── docker/            # Docker container configurations
└── docs/              # Specifications and architectural blueprints
```

---

## 2. Core Trading Strategy

The active trading strategy is the **Manual TradingView SMC Strategy** proven against live reference trades:
- **OB Boundaries**: Direction-specific (`origin.CLOSE` for SHORT top & LONG bottom).
- **BOS Detection**: Causal close beyond opposing origin candle over sliding lookback window ($N=10$).
- **Displacement**: Mode C (Probe $\rightarrow$ Pullback confirmation) with Break $+ 1$ admission.
- **Invalidation**: Wick-based at the distal boundary.
- **Risk & Geometry**: 25% depth entry, $+0.60\%$ fixed TP, $35\%$ SL account risk, $100\times$ cap, portfolio-wide **single-trade lock**.

👉 **Authoritative Specification**: [docs/MANUAL_SMC_STRATEGY.md](docs/MANUAL_SMC_STRATEGY.md)  
👉 **Golden Acceptance Test**: `engine/tests/test_manual_smc_btc_acceptance.py` (**21/21 Passing**)

---

## 3. Quick Start & Local Execution

### Prerequisites
- **Java**: 21+
- **Python**: 3.11+
- **Node.js**: 20+
- **PostgreSQL**: 16+ (or use Docker)
- **Docker & Docker Compose**

### Running with Docker Compose
```bash
# 1. Configure environment
cp .env.example .env

# 2. Start all services (Postgres, Backend, Engine, User App, Developer App)
docker compose up -d
```

### Running Components Individually
```bash
# 1. Start database
docker compose up -d postgres

# 2. Start Java Spring Boot Backend (Port 8080)
cd backend && ./mvnw spring-boot:run

# 3. Start Python Engine (Port 8000)
cd engine
pip install -e .
python -m quantedge

# 4. Start Trader Dashboard (Port 3100)
cd user-app && npm ci && npm run dev

# 5. Start Developer Console (Port 3101)
cd developer-app && npm ci && npm run dev
```

---

## 4. Running the Tests

```bash
# Run the complete Python test suite (845 tests, 100% passing)
python -m pytest engine/tests -v

# Run the Golden BTC Reference Acceptance Test
python -m pytest engine/tests/test_manual_smc_btc_acceptance.py -v

# Run backend unit tests
cd backend && ./mvnw test
```

---

## 5. Authoritative Documentation

- [Manual SMC Strategy Specification](docs/MANUAL_SMC_STRATEGY.md)
- [System Architecture](docs/ARCHITECTURE.md)
- [Backend REST API Specification](docs/API_SPECIFICATION.md)
- [Database & Entity Specification](docs/DATABASE_SPECIFICATION.md)
- [Risk & Account Limits Specification](docs/RISK_SPECIFICATION.md)
- [Security & Encryption Standards](docs/SECURITY.md)

---

## License

MIT
