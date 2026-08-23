# QuantEdge AI — Phase 9 Database Data Map
## Complete PostgreSQL Schema Mapping (Flyway V1–V6), Table Ownership & Retention Rules

---

## 1. Authoritative Database Schema Architecture

The QuantEdge AI persistence layer is managed exclusively by **Flyway Database Migrations** targeting PostgreSQL 16. The database enforces strict multi-tenant isolation where every trading entity maps to a specific `user_id` and `trading_account_id`.

```
Flyway Migration Chain:
V1__initial_schema.sql ──► V2__add_missing_columns.sql ──► V3__add_user_role.sql
         │
         ▼
V4__phase6_state_machine_and_fills.sql ──► V5__phase7_5_ai_signal_enrichment.sql ──► V6__phase8_market_news_events.sql
```

---

## 2. Table-by-Table Schema Map

### 2.1 User & Multi-Account Domain

#### `users` (Owner: Auth Module)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `email` (UNIQUE), `password_hash` (BCrypt), `name`, `role` (`ROLE_USER`, `ROLE_DEVELOPER`, `ROLE_ADMIN`), `is_active`, `email_verified`, `last_login_at`, `created_at`, `updated_at`.
- **Relationships**: Parent of `trading_accounts`, `notification_events`, `journal_entries`, `audit_logs`.
- **Retention**: Permanent until user account deletion.
- **Frontend Usage**: Profile, Auth session, Role gating.

#### `trading_accounts` (Owner: Account Module)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `user_id` (FK -> `users.id`), `name`, `exchange` (`DELTA_INDIA`), `is_active`, `paper_mode`, `balance`, `currency` (`USDT`/`INR`), `leverage`, `algo_enabled`, `kill_switch_active`.
- **Relationships**: Parent of `delta_connections`, `risk_configurations`, `strategy_configurations`, `orders`, `positions`, `trade_records`, `strategy_setups`, `active_trade_locks`.
- **Retention**: Permanent per active trading account.
- **Frontend Usage**: Account selector, Equity header, Algo switch.

#### `delta_connections` (Owner: Security & Connectivity)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `account_id` (FK -> `trading_accounts.id` UNIQUE), `api_key_encrypted` (AES-256), `api_secret_encrypted` (AES-256), `is_connected`, `connection_status` (`CONNECTED`, `VERIFIED`, `FAILED`, `DISCONNECTED`), `last_verified_at`.
- **Security Rule**: API keys and secrets are AES-256 encrypted server-side; NEVER transmitted to frontend or engine in plain text.
- **Retention**: Retained until user disconnects exchange keys.
- **Frontend Usage**: Settings page connection badge.

#### `risk_configurations` & `strategy_configurations`
- **Owner**: Risk & Strategy Modules
- **Key Columns**: `account_id` (FK UNIQUE), `max_risk_pct_per_trade`, `max_leverage`, `daily_loss_limit_usd`, `allowed_symbols`, `timeframes` (`1h`), `min_rr_ratio` (`2.0`).
- **Retention**: Permanent per account.
- **Frontend Usage**: Risk settings panel, Order ticket bounds.

---

### 2.2 Execution & Trading Domain

#### `orders` (Owner: OrderExecutionService)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `account_id` (FK), `client_order_id` (UNIQUE), `exchange_order_id`, `symbol`, `order_type` (`LIMIT`, `MARKET`, `STOP_LIMIT`), `side` (`BUY`, `SELL`), `status` (`PENDING`, `OPEN`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED`, `FAILED`), `price`, `quantity`, `filled_quantity`, `stop_loss`, `take_profit`, `leverage`, `setup_id`.
- **Relationships**: Parent of `order_fills`.
- **Retention**: Permanent audit history.
- **Frontend Usage**: Orders table, Active orders tray.

#### `order_fills` (Owner: Fill Persistence)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `order_id` (FK -> `orders.id`), `account_id` (FK), `fill_id`, `exchange_fill_id`, `symbol`, `side`, `fill_price`, `fill_quantity`, `fee`, `fee_currency`, `filled_at`.
- **Retention**: Permanent audit ledger.
- **Frontend Usage**: Execution Fills ledger, Slippage analysis.

#### `positions` (Owner: Position Management)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `account_id` (FK), `symbol`, `side` (`LONG`, `SHORT`), `size`, `entry_price`, `current_price`, `liquidation_price`, `unrealized_pnl`, `realized_pnl`, `status` (`OPEN`, `CLOSED`, `LIQUIDATED`), `leverage`, `margin`.
- **Retention**: Permanent position history.
- **Frontend Usage**: Positions tab, Real-time P&L cards.

#### `trade_records` (Owner: Performance & Accounting)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `account_id` (FK), `setup_id`, `symbol`, `side`, `entry_time`, `exit_time`, `entry_price`, `exit_price`, `quantity`, `pnl`, `net_pnl`, `fees_paid`, `exit_reason` (`TAKE_PROFIT`, `STOP_LOSS`, `MANUAL`, `KILL_SWITCH`).
- **Retention**: Permanent accounting history.
- **Frontend Usage**: Closed Trades History table, Analytics dashboard.

#### `active_trade_locks` (Owner: Single Trade Lock)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `account_id` (FK), `setup_id`, `symbol`, `lock_state` (`LOCKED`, `RELEASING`), `acquired_at`.
- **Lifecycle**: Acquired atomically when order enters `PENDING`; released when trade closes or fails.
- **Frontend Usage**: Developer App active lock inspector.

#### `strategy_setups` (Owner: Strategy Engine)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `account_id` (FK), `setup_id` (UNIQUE), `symbol`, `timeframe` (`1h`), `direction` (`LONG`, `SHORT`), `bias`, `entry_price`, `stop_loss`, `take_profit_1`, `take_profit_2`, `rr_ratio`, `status` (`QUALIFIED`, `ACTIVE`, `COMPLETED`, `INVALIDATED`), `confidence_score`.
- **Retention**: Permanent setup ledger.
- **Frontend Usage**: Signals Radar page, Chart setup overlays.

---

### 2.3 Intelligence & Market Data Domain

#### `ai_signal_enrichments` (Owner: AI Intelligence Layer)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `account_id` (FK), `setup_id` (UNIQUE), `symbol`, `direction`, `technical_score` (0–100), `market_regime_score` (0–100), `sentiment_score` (0–100), `composite_confidence_score` (0–100), `recommendation` (`HIGH_CONFIDENCE_LONG`, etc.), `reasoning_summary`, `regime` (`TRENDING_BULLISH`, `RANGING`, etc.), `macro_event_risk` (`LOW`, `MODERATE`, `HIGH`), `generated_at`.
- **Retention**: Permanent signal intelligence history.
- **Frontend Usage**: AI Radar gauge, Reasoning card.

#### `news_articles` (Owner: News Ingestion)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `title`, `summary`, `source`, `url`, `category` (`CRYPTO`, `FINANCE`, `MARKETS`, `CENTRAL_BANKS`, `REGULATION`, `ECONOMY`, `COMMODITIES`, `MACRO`), `importance` (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`), `relevant_symbols`, `sentiment` (`BULLISH`, `BEARISH`, `NEUTRAL`), `fingerprint` (SHA-256 UNIQUE), `published_at`, `expires_at` (`published_at + 7 days`).
- **Retention Rule**: **Strict 7-Day TTL** (`expires_at = published_at + 7 days`). Expired articles are purged hourly via `@Scheduled(cron = "0 0 * * * *")`.
- **Frontend Usage**: Financial News Feed, Ticker banner.

#### `economic_events` (Owner: Economic Calendar)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `event_name`, `country` (`US`, `IN`, `EU`, `GB`, `JP`, `CN`, `CA`, `AU`), `currency`, `category` (`INFLATION`, `CENTRAL_BANK`, `EMPLOYMENT`, `GROWTH`, `TRADE`), `importance` (`HIGH`, `MEDIUM`, `LOW`), `scheduled_at`, `previous_value`, `forecast_value`, `actual_value`, `status` (`UPCOMING`, `IN_PROGRESS`, `COMPLETED`), `source`, `source_url`, `provider_event_id` (UNIQUE), `expires_at`.
- **Retention Rule**: **Strict 24-Hour Post-Event TTL** (`expires_at = actual_release_time + 24 hours` for completed events, or `scheduled_at + 24 hours` for upcoming events). Purged hourly.
- **Frontend Usage**: 15-Day Macroeconomic Calendar.

#### `notification_events` (Owner: Notifications)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `user_id` (FK -> `users.id`), `type` (`NEWS_ALERT`, `ECONOMIC_EVENT_ALERT`, `TRADE_ALERT`), `title`, `message`, `reference_id`, `severity` (`CRITICAL`, `HIGH`, `INFO`), `is_read` (BOOLEAN), `read_at`, `created_at`.
- **Retention**: 30-day retention per user.
- **Frontend Usage**: In-App Notification Drawer.

#### `audit_logs` (Owner: Developer / Security)
- **Primary Key**: `id` (UUID string)
- **Key Columns**: `user_id` (FK NULLABLE), `action`, `details`, `ip_address`, `created_at`.
- **Retention**: Permanent compliance audit trail.
- **Frontend Usage**: Developer App Sanitized Logs Viewer.

---

## 3. Database Invariant Confirmation
1. **Single Source of Truth**: All User App and Developer App features query this unified schema. **No separate or duplicated database exists.**
2. **Root `database/` Directory**: Verified **NOT REQUIRED**. All schema definitions are version-controlled in `backend/src/main/resources/db/migration/`.
