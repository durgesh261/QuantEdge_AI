# Phase 5.12 — PostgreSQL Runtime Integration

## Overview

Phase 5.12 replaces the in-memory `export_state`/`load_state` persistence pattern
with real PostgreSQL persistence through the Java backend. All authoritative trading
state now survives application restarts, crashes, WebSocket disconnects, and duplicate
signals.

---

## Architecture

```
Frontend (React / TypeScript)
       │ REST API (user-facing DTOs)
       ▼
Java Backend (Spring Boot 3.2 / JPA)
       │ Engine API (internal, engine-only)
       │ Flyway migrations
       │ @Transactional boundaries
       ▼
PostgreSQL 16
       ▲
       │ HTTP (BackendClient)
Python Trading Engine
       │
       ▼
Delta Exchange (market data + order execution)
```

**Responsibility split:**
| Layer | Owns |
|-------|------|
| Python engine | SMC logic, signal generation, entry/exit calculations |
| Java backend | Credential storage, order execution gateway, persistence |
| PostgreSQL | Authoritative state (balance, lock, P&L, configuration) |
| Frontend | Display only — no trading logic |

---

## Database Configuration

### Environment Variables

| Variable | Purpose | Required in production |
|----------|---------|----------------------|
| `SPRING_DATASOURCE_URL` | JDBC URL | ✅ |
| `SPRING_DATASOURCE_USERNAME` | DB user | ✅ |
| `SPRING_DATASOURCE_PASSWORD` | DB password | ✅ |
| `DB_POOL_SIZE` | HikariCP pool size (default: 20) | Optional |
| `JWT_SECRET` | JWT signing key (min 256 bits) | ✅ |
| `ENCRYPTION_KEY` | AES-256 key for Delta credentials | ✅ |
| `BACKEND_API_KEY` | Internal engine API key | ✅ |
| `BACKEND_ACCOUNT_ID` | Trading account UUID for engine | ✅ |

> [!CAUTION]
> NEVER commit real values for any of these variables. Use `.env.example` as
> a template. The dev fallback values in `application.yml` are for local
> `docker-compose` only and are never valid in production.

### Local Development (docker-compose)

```bash
# Start PostgreSQL + backend + engine + frontend
docker-compose up -d

# Or start only PostgreSQL for backend development
docker-compose up -d postgres
```

Default dev credentials (docker-compose only):
- Database: `quantedge`
- User: `quantifiedge`  
- Password: `quantifiedge_dev` (set via `SPRING_DATASOURCE_PASSWORD` env var)

---

## Flyway Migrations

Migrations are in `backend/src/main/resources/db/migration/` and run automatically
on backend startup via Spring Boot Flyway auto-configuration.

| File | Purpose |
|------|---------|
| `V1__initial_schema.sql` | Core tables: users, trading_accounts, delta_connections, risk_configurations, orders, positions, order_blocks, journal_entries, audit_logs |
| `V2__add_missing_columns.sql` | Additive columns + 3 new tables (see below) |

### V2 Additions

**Columns added to `trading_accounts`:**
- `algo_enabled BOOLEAN NOT NULL DEFAULT FALSE` ← fail-safe
- `kill_switch_active BOOLEAN NOT NULL DEFAULT TRUE` ← fail-safe
- `total_equity`, `available_balance`, `margin_used`, `last_synced_at`

**Columns added to `risk_configurations`:**
- `version INTEGER NOT NULL DEFAULT 1`
- `algo_enabled BOOLEAN NOT NULL DEFAULT FALSE` ← fail-safe
- `kill_switch_active BOOLEAN NOT NULL DEFAULT TRUE` ← fail-safe

**New table: `strategy_setups`**
Immutable per-trade configuration snapshots. One row per trade setup.
Stores `entry_price`, `stop_loss`, `take_profit`, `configuration_version`.
Config changes after trade open do NOT affect the snapshot.

**New table: `active_trade_locks`**
DB-enforced one-trade-at-a-time per account:
```sql
CREATE UNIQUE INDEX idx_active_trade_locks_account_active
    ON active_trade_locks(trading_account_id)
    WHERE released_at IS NULL;
```
This partial unique index makes it physically impossible for PostgreSQL to have
two active (unreleased) locks for the same trading account, regardless of how
many processes or retries attempt to create one simultaneously.

**New table: `trade_records`**
Authoritative per-trade record: entry, exit, gross P&L, fee breakdown, net P&L,
compounded balance. One row per complete trade lifecycle.

---

## Persistence Lifecycle

### On Application Startup
1. Flyway runs V1 (if not already applied) then V2
2. Spring Boot JPA validates schema against entities (`ddl-auto: validate`)
3. Python engine calls `GET /api/engine/state/{accountId}`
4. If `hasActiveTrade=true` → engine reconciles against Delta before proceeding
5. If `hasActiveTrade=false` → engine starts fresh market scan

### On Trade Open (atomic)
```
Python: calls POST /api/engine/trade/open/{accountId}
  Java:
    1. CHECK kill_switch_active=false, algo_enabled=true
    2. INSERT active_trade_locks (partial unique index fires on duplicate)
    3. INSERT trade_records (state=OPEN, pre_trade_balance)
    4. Both writes in one @Transactional
```

### On Trade Close (atomic)
```
Python: calls POST /api/engine/trade/close/{accountId}
  Java:
    1. UPDATE trade_records: exit_price, gross_pnl, fees, funding, net_pnl, post_balance
    2. UPDATE trading_accounts: current_balance = post_trade_balance
    3. UPDATE active_trade_locks: released_at = NOW()
    4. All three writes in one @Transactional
    5. Rollback on any failure → no orphaned lock, no inconsistent balance
```

### Net P&L Formula (enforced by Java, never by frontend)
```
net_pnl = gross_pnl - trading_fees - funding_costs - other_costs
post_trade_balance = pre_trade_balance + net_pnl
post_trade_balance = max(post_trade_balance, 0)  -- floor at zero

If Delta reports an authoritative exchange balance:
  post_trade_balance = authoritative_exchange_balance
```

---

## Single-Trade Enforcement

| Layer | Mechanism |
|-------|-----------|
| DB | Partial unique index on `active_trade_locks(account_id) WHERE released_at IS NULL` |
| Java | `SELECT FOR UPDATE` in `findActiveLockByAccountId`, `DataIntegrityViolationException` → `TradeLockException` |
| Python | Checks `notify_trade_open()` result; aborts if `success=false` |
| Frontend | Read-only display; cannot submit trade orders directly |

The DB constraint is the authoritative enforcement. It cannot be bypassed by
any amount of retry, restart, or duplicate signal.

---

## 100% Capital Allocation Flow

```
Latest post_trade_balance (from trade_records)
        ▼
    or account.current_balance (if no closed trades)
        ▼
GET /api/engine/capital/{accountId}  ← Python reads this
        ▼
100% allocated to next trade
        ▼
stop_distance = abs(entry - OB_edge) / entry
leverage = floor(0.35 / stop_distance)  [min: 1]
        ▼
Delta order execution
```

---

## Crash / Restart Recovery

| Scenario | Recovery |
|----------|----------|
| A — No active trade | Engine reads `hasActiveTrade=false`, starts scan immediately |
| B — Entry pending | Engine reads lock, reconciles with Delta before any new trade |
| C — Filled, no SL/TP yet | Engine reads lock state, places SL/TP, does NOT re-enter |
| D — SL/TP placed | Engine reads `PROTECTED_POSITION` state, monitors |
| E — Position open | Engine reconnects WS, resumes monitoring from DB state |
| F — Just after TP | If close record exists in DB, returns post-balance; no new entry |
| G — Just after SL | Same as F; net loss is persisted; scanner runs fresh |
| H — WS disconnect | State is in DB; engine re-reads on reconnect; no memory loss |
| I — Backend restart | Python engine retries with backoff; reads DB state on success |
| J — Engine restart | Engine reads DB state on startup; lock status is authoritative |

---

## Transaction Boundaries

All multi-table writes are wrapped in `@Transactional` (Spring):

| Operation | Tables written | Atomic? |
|-----------|----------------|---------|
| `openTrade` | `active_trade_locks` + `trade_records` | ✅ |
| `closeTrade` | `trade_records` + `trading_accounts` + `active_trade_locks` | ✅ |
| `forceReleaseLock` | `active_trade_locks` + `audit_logs` | ✅ |
| `updateLockState` | `active_trade_locks` | ✅ |

Partial failures roll back the entire operation. A failure in step 2 of `closeTrade`
will not leave the lock released while the balance is still at the pre-trade value.

---

## Security

| Item | Status |
|------|--------|
| DB password in source code | ✅ Fixed — `${SPRING_DATASOURCE_PASSWORD:...}` |
| JWT secret | ✅ `${JWT_SECRET:dev-fallback}` |
| Delta API credentials | ✅ Encrypted at rest in `delta_connections` |
| Credentials returned to frontend | ✅ Masked (last 4 chars) |
| Python engine holds credentials | ✅ Never — all credentials are Java-only |
| Cross-user account access | ✅ Ownership checked in `OrderExecutionService` |
| Engine API auth | Engine API key via `X-Engine-Api-Key` header |
| All trade actions audited | ✅ `audit_logs` table; non-blocking |

---

## Java ↔ Python Contract

The `EngineStateController` (`/api/engine/*`) is the contract boundary:

| Endpoint | Called by Python when |
|----------|-----------------------|
| `GET /api/engine/state/{accountId}` | Engine startup, after each scan |
| `GET /api/engine/capital/{accountId}` | Before sizing next trade |
| `POST /api/engine/trade/open/{accountId}` | Before submitting entry order |
| `POST /api/engine/trade/close/{accountId}` | After Delta confirms position closed |
| `POST /api/engine/trade/lock-state/{accountId}` | On state transitions (filled, SL/TP placed) |
| `POST /api/engine/trade/force-release/{accountId}` | After reconciliation confirms no position |

**Frontend NEVER calls `/api/engine/*` endpoints.**

---

## Testing Procedure

### Python engine tests (no DB required)
```bash
cd engine
python -m pytest tests/test_phase5_12_postgresql_persistence.py -v
python -m pytest tests/ -q  # full regression
```

### Java integration tests (requires Docker)
```bash
cd backend
./mvnw test -Dtest=Phase512PersistenceIntegrationTest
```

### Manual Flyway validation
```bash
docker-compose up -d postgres
cd backend && ./mvnw flyway:info   # shows applied migrations
./mvnw flyway:validate             # confirms schema matches entities
```

### Migration idempotency (Flyway handles this automatically)
Flyway tracks applied migrations in `flyway_schema_history`. Re-running the backend
will NOT re-apply V1 or V2 if they are already applied.

### Frozen SMC baseline
```bash
git diff b8095dc -- engine/src/quantedge/smc/
# Expected output: (empty — zero diffs)
```

---

## Environment Setup Template

Copy `.env.example` to `.env` and fill in:

```bash
# Required in production
SPRING_DATASOURCE_URL=jdbc:postgresql://<host>:5432/<dbname>
SPRING_DATASOURCE_USERNAME=<user>
SPRING_DATASOURCE_PASSWORD=<password>
JWT_SECRET=<openssl rand -base64 32>
ENCRYPTION_KEY=<openssl rand -base64 32>
BACKEND_API_KEY=<random key shared with Python engine>
BACKEND_ACCOUNT_ID=<UUID of the trading account>
```
