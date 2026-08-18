# QuantEdge AI V2

Clean architecture implementation for QuantEdge AI trading platform.

## Architecture

```
QuantEdge AI V2
├── frontend/          # React + TypeScript (UI only)
├── backend/           # Java + Spring Boot (Authoritative backend)
├── engine/            # Python (Market intelligence & strategy research)
├── database/          # PostgreSQL migrations
├── tests/             # Integration & E2E tests
├── docs/              # Architecture & specification documents
└── docker/            # Docker configuration
```

## Technology Stack

- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS
- **Backend**: Java 21 + Spring Boot 3.x + PostgreSQL
- **Engine**: Python 3.11+ + Poetry/UV + pandas/numpy/ta-lib
- **Database**: PostgreSQL 16+
- **Exchange**: Delta Exchange API

## Legacy Reference

The previous implementation (Node.js/Express/Prisma) is preserved in:
- Git tag: `quantedge-v1-legacy`
- Directory: `legacy/`

## Quick Start

### Prerequisites

- Java 21+
- Node.js 20+
- Python 3.11+
- PostgreSQL 16+
- Docker & Docker Compose (optional)

### Development Setup

```bash
# Start infrastructure
docker-compose up -d postgres

# Backend (Spring Boot)
cd backend && ./mvnw spring-boot:run

# Python Engine
cd engine && uv run python -m quantedge

# Frontend
cd frontend && npm install && npm run dev
```

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