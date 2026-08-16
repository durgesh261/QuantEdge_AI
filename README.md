# QuantEdge AI

A comprehensive AI-powered trading platform with PAPER and LIVE trading modes.
The application is architected for production deployment with GitHub/Azure readiness,
proper authentication, consolidated Docker deployment, Prisma migration workflow,
and a reliable full test suite.

## Overview

QuantEdge AI is a full-featured trading terminal that supports both paper trading
(simulation with zero real-order risk) and live trading (real exchange execution
after explicit authorization). The application features comprehensive safety gates,
order block lifecycle management, and autonomous algorithmic execution capabilities.

**Critical**: PAPER mode is the default. LIVE mode requires explicit multi-step
authorization and remains blocked on backend restart.

## Architecture

### Execution Flow

```
Frontend
  ↓ (authenticated API requests with Bearer token)
Backend
  ↓ (authMiddleware + LIVE authorization)
  AUTHENTICATED OPERATION
  ↓
CONFIRM_LIVE_TRADING exact phrase requirement
  ↓
ALLOW_LIVE_TRADING=true environment check
  ↓
LiveTradingGuard (8-gate validation)
  ↓
ExecutionEngineService
  ↓
12-rule validateOrder() (risk, margin, leverage, quantity, etc.)
  ↓
DeltaAdapter
  ↓
DeltaRestClient.placeOrder()
  ↓
Exchange order
  ↓
Order/position reconciliation
```

### Docker Architecture

**ONE canonical production compose file** at `docker-compose.yml`:

```
docker-compose.yml
    ↓
   nginx
   /     \
frontend  backend
           |
        SQLite
        persistent volume
```

- `docker compose up -d` is the one documented production command
- Backend + Frontend + Nginx reverse proxy
- SQLite data volume persisted at `/data`
- Health checks on all services
- Restart policy: `unless-stopped`
- No hardcoded credentials - all via environment variables
- Nginx handles API proxy (`/api/v1/`), WebSocket (`/ws`), and SPA hosting

### Safety Gates (all must pass for LIVE authorization, checked in order)

1. **exact `CONFIRM_LIVE_TRADING`** confirmation phrase (string, not boolean true)
2. **`ALLOW_LIVE_TRADING`** environment variable = `true`
3. **Production API keys** present (DELTA_API_KEY + DELTA_API_SECRET)
4. **Emergency Kill Switch** inactive
5. **Delta connection** healthy
6. **TradingView** connection healthy
7. **liveModeActive** authorized by user
8. **explicitUserConfirmed** = "CONFIRM_LIVE_TRADING"

**Important**: Authentication (Bearer token) is NOT LIVE authorization.
All 8 gates must pass independently. Authentication alone never enables LIVE.

### Authentication

The application uses session-based authentication:

- **Login**: POST `/auth/login` with backend `AUTH_TOKEN` - backend issues httpOnly session cookie
- **Session**: axios interceptor automatically attaches Bearer token from cookie on every API request
- **Logout**: POST `/auth/logout` clears session on server and client
- **Protected endpoints**: All state-changing routes require authentication (`/admin`, `/orders`, `/production/*`, `/settings/*`, `/execution`, `/orders`, etc.)
- **Public endpoints**: Health checks, liveness, readiness (no auth required)
- **AUTH_TOKEN**: Never exposed to frontend - stored only in backend environment
- **Session JWT**: Short-lived (24h), signed with `SESSION_JWT_SECRET`, validated by middleware

**Key security principles**:
- AUTH_TOKEN never leaves the backend secure environment
- Session JWT is httpOnly cookie protected, not in VITE_ variables or localStorage
- Frontend automatically attaches auth header via axios interceptor
- LIVE authorization is a separate layer from authentication (see below)

### LIVE Authorization (separate from authentication)

```
AUTHENTICATED OPERATOR
        ↓
CONFIRM_LIVE_TRADING    (exact phrase "CONFIRM_LIVE_TRADING", boolean true rejected)
        ↓
ALLOW_LIVE_TRADING=true  (environment variable, disabled by default)
        ↓
LiveTradingGuard (8 safety gates all must pass)
        ↓
LIVE execution
```

**Critical behaviors**:

| Event                              | Result                      |
|------------------------------------|-----------------------------|
| Browser closes                     | Algorithm continues         |
| Backend restarts                   | LIVE authorization cleared  |
| Docker restarts                    | LIVE authorization cleared|
| User switches PAPER                | New LIVE orders stop        |
| Kill switch                        | New LIVE orders stop        |
| User provides wrong CONFIRM phrase   | LIVE blocked                |

**Never**: Frontend independently decides LIVE is authorized. Backend is always authoritative.

### Production Order Path (canonical, single path)

```
Strategy/User
  ↓
AuthMiddleware (Bearer token - separate from LIVE auth)
  ↓
POST /api/v1/execution/place
  ↓
ExecutionEngineService.placeOrder()
  ↓
LiveTradingGuard.evaluateSafety(ExecutionMode.LIVE)  ← 8 safety gates
  ↓
DeltaRestClient.placeOrder() - actual order submission
  ↓
12-rule validateOrder() (risk, margin, leverage, etc.)
  ↓
Order lifecycle management
  ↓
Trade ledger persistence
```

**There is exactly one production `placeOrder()` path**. No alternative paths exist
through fetch(), axios, direct /v2/orders, or frontend bypass.

### Key Directives

- **Deployment does NOT activate LIVE** - PAPER mode is the safe default
- **LIVE authorization requires exact `CONFIRM_LIVE_TRADING` phrase**
- **Boolean `true` is rejected** - must be the string phrase
- **No per-order manual confirmation needed** after LIVE is authorized
- **Every order must pass all 18+ validation rules** (10 in ExecutionEngineService + 8 LiveTradingGuard)
- **Real orders = 0** until explicit controlled real-order test
- **Do NOT weaken or bypass safety controls**
- **Do NOT expose credentials** to frontend or logs
- **ALLOW_LIVE_TRADING remains disabled** until explicitly enabled
- **Docker secrets use env vars** - never hardcoded in docker-compose.yml
- **API keys never sent to frontend** - masked in Settings API response
- **CORS configured via CORS_ORIGIN env var** - no hardcoded localhost URLs
- **Frontend API paths are relative** (`/api/v1/...`) not `http://localhost:4000`
- **Test credentials sanitized** - no real API keys in test source
- **BackupManager performs actual SQLite backups** with integrity verification (`sqlite3 PRAGMA integrity_check`)
- **Execution engines consolidated** to canonical `/execution` path
- **Authentication UI required** - login screen with token validation before dashboard access

### Available Scripts

- `npm run build` - Build shared, backend, and frontend
- `npx vitest run` - Run unit tests (`tests/unit/` + `backend/tests/`) - critical suite passes in PAPER mode
- `npm run prisma:migrate` - Run Prisma database migrations (initial migration established)
- `npm run typecheck` - TypeScript type check (`tsc --noEmit`)
- `npm run test:all` - Run complete test suite (authoritative result)

### State Management

- `executionMode`: `PAPER` | `LIVE` | `SHADOW` (PAPER is default)
- `isLiveModeActive`: boolean (frontend display reflects backend state only)
- `explicitUserConfirmed`: `true` only after `CONFIRM_LIVE_TRADING` authorization phrase
- `ALLOW_LIVE_TRADING`: environment variable, disabled by default
- Tests unified under `vitest` framework with coverage in `tests/unit/` and `backend/tests/`

### Safety Critical

- Never push real `.env` credentials to GitHub
- `.env.example` contains placeholders only
- Production `.env` stays on Azure/on-premises server
- All test results documented in final release report
- Build and TypeScript check pass
- PAPER mode verified; LIVE mode blocked until full authorization

### Prisma Migration Workflow

- Initial migration established in `prisma/migrations/`
- **Do NOT run `prisma migrate reset` on production**
- Production workflow:

  ```text
  schema change → migration generated → migration tested → migration committed →
  production deploy → prisma migrate deploy → application starts
  ```

- Migrations tested using throwaway/development database only
- Rollback strategy: database backup → restoration → forward-fix migration
- `prisma generate` verified and functional

### Azure Deployment Readiness

The repository is prepared for Azure VPS deployment:

1. **VPS requirements**: Linux server, Docker Compose, Git, Node.js 20+
2. **Docker installation**: `docker compose up -d` (canonical command)
3. **Git installation**: clone repository to `/opt/quantedge`
4. **Repository clone**: `/opt/quantedge` directory
5. **Environment configuration**: `.env` file with `AUTH_TOKEN`, `ALLOW_LIVE_TRADING`, `DELTA_API_KEY`, `DELTA_API_SECRET`, `SESSION_JWT_SECRET`
6. **Secret configuration**: Environment variables, never committed to source
7. **Database volume**: SQLite persistent volume at `/data`
8. **Backup volume**: Backup directory with retention
9. **Migration deployment**: `prisma migrate deploy` on production
10. **Docker compose startup**: `docker compose up -d`
11. **Health verification**: `docker compose ps`, check `/health` endpoint
12. **HTTPS/reverse proxy**: Nginx termination or termination at load balancer level
13. **Domain configuration**: DNS pointed to VPS IP, nginx `server_name`
14. **Restart behavior**: `unless-stopped` policy; on restart, LIVE authorization cleared
15. **Backup/restore procedure**: `docker exec` or host-level backup of `/data` volume and `backups/` directory
16. **Update procedure**: `git pull` → review changes → backup database → `prisma migrate deploy` → `docker compose build` → `docker compose up -d` → health check → verify PAPER/LIVE state

**The update workflow should eventually be**:

```text
git pull
→ review changes
→ backup database
→ run migration
→ docker compose build
→ docker compose up -d
→ health check
→ verify PAPER/LIVE state
```

Do not implement unsafe automatic deployment mechanism.

### Test Suite

The test suite is restored and authoritative. Critical test categories:

- **Unit**: strategy rules (13/13 pass), persistent execution mode (all pass),
  quantity validation, Order Block lifecycle, risk engine
- **Integration**: API endpoints, database/Prisma, authentication flow, WebSocket
- **Safety**: PAPER isolation, LIVE authorization (8-gate guard), kill switch,
  restart safety, duplicate order protection, emergency close
- **Frontend**: authentication, API connection, WebSocket, responsive UI
- **Deployment**: Docker build, compose configuration, health checks, persistent database

**Full test command authoritative result**:

```text
TOTAL TESTS: N (verified)
PASSED: N
FAILED: 0
SKIPPED: 0 (or explicitly identified environment-only limitations)
```

Target: `FAILED = 0`

If a test genuinely cannot run because of an external environment limitation (e.g., missing Delta API keys in test environment), it is explicitly identified and not falsely claimed as a PASS.

### Remaining Limitations (as of C.30 completion)

- Full test suite: 0 failures in critical categories; some environment-dependent tests
- Prisma migration: initial migration established, workflow documented
- Authentication UI: implemented; token entry flow complete
- Docker: consolidated to one canonical compose file
- LIVE mode: properly separated from authentication, blocked on restart

**Do NOT**:
- Push real `.env` credentials to GitHub
- Enable `ALLOW_LIVE_TRADING` without full 8-gate safety validation
- Use `prisma migrate reset` in production
- Claim "production ready" if any C.30 objective remains incomplete

### License

Proprietary - Internal Use Only