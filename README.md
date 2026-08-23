# QuantEdge AI V2

Clean architecture implementation for QuantEdge AI institutional crypto trading platform.

## Architecture

```
QuantEdge AI V2
├── backend/           # Java 21 + Spring Boot 3.2 (Authoritative execution gateway)
├── engine/            # Python 3.11+ (SMC market intelligence & strategy research)
├── user-app/          # React 18 + TypeScript (Trader / End-User web terminal — Port 3100)
├── developer-app/     # React 18 + TypeScript (Operator / Admin / Dev console — Port 3101)
├── docker/            # Multi-service container orchestration
└── docs/              # Specifications and architectural blueprints
```

## Technology Stack

- **Trader Web Terminal (`user-app`)**: React 18 + TypeScript + Vite + Tailwind CSS + Lightweight Charts (Port 3100)
- **Operator Console (`developer-app`)**: React 18 + TypeScript + Vite + Tailwind CSS (Port 3101)
- **Backend API Gateway**: Java 21 + Spring Boot 3.x + Spring Security + PostgreSQL JPA (Port 8080)
- **Trading Engine**: Python 3.11+ + Poetry/UV + pandas/numpy (Port 8000)
- **Database**: PostgreSQL 16+ + Redis 7+
- **Exchange Gateway**: Delta Exchange India REST / WebSocket (Server-Side only)

## Quick Start

### Prerequisites

- Java 21+
- Node.js 20+
- Python 3.11+
- PostgreSQL 16+
- Docker & Docker Compose (optional)

### Development Setup

```bash
# Configure required secrets and local ports. At minimum, replace
# PYTHON_ENGINE_API_KEY before starting Compose.
cp .env.example .env

# Start infrastructure
docker compose up -d postgres redis

# Backend (Spring Boot)
cd backend && ./mvnw spring-boot:run

# Python Engine
cd engine && uv run python -m quantedge

# Trader Application (Port 3100)
cd user-app && npm ci && npm run dev

# Developer / Operator Console (Port 3101)
cd developer-app && npm ci && npm run dev
```

The public API is served under `http://localhost:8080/api`. Both browser
applications proxy `/api/*` to that backend during development and expose it
through Nginx in Compose. The engine health endpoint is
`http://localhost:8000/health`.

For production, set `SPRING_PROFILES_ACTIVE=production`, provide unique
`JWT_SECRET`, `ENCRYPTION_KEY`, and `PYTHON_ENGINE_API_KEY` values, and set
`COOKIE_SECURE=true` behind HTTPS. The backend refuses to start with missing
production secrets or insecure authentication cookies.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [SMC Specification](docs/SMC_SPECIFICATION.md)
- [Strategy Specification](docs/STRATEGY_SPECIFICATION.md)
- [Risk Specification](docs/RISK_SPECIFICATION.md)
- [Database Specification](docs/DATABASE_SPECIFICATION.md)
- [API Specification](docs/API_SPECIFICATION.md)
- [Security](docs/SECURITY.md)

## License

MIT
