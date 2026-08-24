-- =============================================================================
-- QuantEdge AI — V2 Schema Addendum
-- Migration: V2__add_missing_columns.sql
-- Compatible: PostgreSQL 15+  |  Flyway baseline ≥ V1
-- =============================================================================
-- PURPOSE:
--   Adds the columns and tables that JPA entities expect but V1 did not define.
--   All ALTER statements are additive (no existing data is touched or deleted).
--   All fail-safe defaults (algo_enabled=false, kill_switch_active=true) are
--   enforced at the DDL level and verified by a safety assertion at the end.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. trading_accounts — add operational columns missing from V1
-- ---------------------------------------------------------------------------
ALTER TABLE trading_accounts
    ADD COLUMN IF NOT EXISTS total_equity       DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS available_balance  DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS margin_used        DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_synced_at     TIMESTAMP WITH TIME ZONE,
    -- CRITICAL FAIL-SAFE DEFAULTS
    ADD COLUMN IF NOT EXISTS algo_enabled       BOOLEAN         NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS kill_switch_active BOOLEAN         NOT NULL DEFAULT TRUE;

-- ---------------------------------------------------------------------------
-- 2. risk_configurations — add versioning and safety columns missing from V1
-- ---------------------------------------------------------------------------
ALTER TABLE risk_configurations
    ADD COLUMN IF NOT EXISTS version            INTEGER         NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS take_profit_percent DECIMAL(5, 2)  NOT NULL DEFAULT 60.00,
    ADD COLUMN IF NOT EXISTS stop_loss_percent  DECIMAL(5, 2)  NOT NULL DEFAULT 1.00,
    ADD COLUMN IF NOT EXISTS minimum_risk_reward DECIMAL(5, 2)  NOT NULL DEFAULT 1.50,
    -- CRITICAL FAIL-SAFE DEFAULTS
    ADD COLUMN IF NOT EXISTS algo_enabled       BOOLEAN         NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS kill_switch_active BOOLEAN         NOT NULL DEFAULT TRUE;

-- ---------------------------------------------------------------------------
-- 2b. orders — add setup_id missing from V1
-- ---------------------------------------------------------------------------
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS setup_id VARCHAR(100);

CREATE INDEX IF NOT EXISTS idx_orders_setup_id ON orders(setup_id);

-- ---------------------------------------------------------------------------
-- 2c. audit_logs — align BaseEntity auditing and details columns with JPA entity
-- ---------------------------------------------------------------------------
ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS details    TEXT;

ALTER TABLE audit_logs
    ALTER COLUMN resource_id TYPE VARCHAR(100) USING resource_id::text,
    ALTER COLUMN ip_address TYPE VARCHAR(50) USING ip_address::text;

CREATE TRIGGER update_audit_logs_updated_at
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- 3. strategy_setups — immutable per-trade configuration snapshots
--    (JPA entity StrategySetupRecord.java maps to this table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_setups (
    id                    UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id    UUID            REFERENCES trading_accounts(id) ON DELETE SET NULL,
    setup_id              VARCHAR(100)    NOT NULL UNIQUE,
    symbol                VARCHAR(20)     NOT NULL,
    direction             VARCHAR(10)     NOT NULL,
    timeframe             VARCHAR(10)     NOT NULL DEFAULT '1h',
    setup_state           VARCHAR(30)     NOT NULL DEFAULT 'TRADE_SETUP_READY',
    strategy_name         VARCHAR(50)     NOT NULL DEFAULT 'SMC',
    strategy_version      VARCHAR(20)     NOT NULL DEFAULT '2.1',
    configuration_version INTEGER                  DEFAULT 1,
    entry_price           DECIMAL(20, 8)  NOT NULL,
    stop_loss             DECIMAL(20, 8)  NOT NULL,
    take_profit           DECIMAL(20, 8)  NOT NULL,
    risk_distance         DECIMAL(20, 8),
    reward_distance       DECIMAL(20, 8),
    risk_reward           DECIMAL(10, 4),
    confidence            DECIMAL(5, 2),
    expires_at            TIMESTAMP WITH TIME ZONE,
    created_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_setups_setup_id
    ON strategy_setups(setup_id);
CREATE INDEX IF NOT EXISTS idx_strategy_setups_account
    ON strategy_setups(trading_account_id);
CREATE INDEX IF NOT EXISTS idx_strategy_setups_state
    ON strategy_setups(setup_state);

CREATE TRIGGER update_strategy_setups_updated_at
    BEFORE UPDATE ON strategy_setups
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- 4. active_trade_locks — DB-enforced one-trade-at-a-time per account
--    The UNIQUE constraint on (trading_account_id) WHERE released_at IS NULL
--    prevents two simultaneous open trades at the database level, surviving
--    any process restart, crash, or WebSocket reconnect.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS active_trade_locks (
    id                    UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id    UUID            NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    setup_id              VARCHAR(100)    NOT NULL,
    symbol                VARCHAR(20)     NOT NULL,
    lifecycle_state       VARCHAR(30)     NOT NULL DEFAULT 'ENTRY_SUBMITTED',
    acquired_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    released_at           TIMESTAMP WITH TIME ZONE,   -- NULL = lock is active
    release_reason        VARCHAR(50),
    force_released        BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Partial unique index: only one active (unreleased) lock per account at a time.
-- This is the DB-level enforcement of the one-trade-at-a-time rule.
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_trade_locks_account_active
    ON active_trade_locks(trading_account_id)
    WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_active_trade_locks_setup_id
    ON active_trade_locks(setup_id);
CREATE INDEX IF NOT EXISTS idx_active_trade_locks_released_at
    ON active_trade_locks(released_at);

CREATE TRIGGER update_active_trade_locks_updated_at
    BEFORE UPDATE ON active_trade_locks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- 5. trade_records — authoritative net P&L, fees, compounded balance per trade
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_records (
    id                    UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id    UUID            NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    setup_id              VARCHAR(100)    NOT NULL,
    symbol                VARCHAR(20)     NOT NULL,
    direction             VARCHAR(10)     NOT NULL,
    entry_price           DECIMAL(20, 8)  NOT NULL,
    exit_price            DECIMAL(20, 8),
    quantity              DECIMAL(20, 8)  NOT NULL,
    leverage              INTEGER         NOT NULL,
    -- P&L breakdown (all reconciled from Delta authoritative data)
    gross_pnl             DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    trading_fees          DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    funding_costs         DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    other_costs           DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    net_pnl               DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    -- Net formula: net_pnl = gross_pnl - trading_fees - funding_costs - other_costs
    -- Balance compounding
    pre_trade_balance     DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    post_trade_balance    DECIMAL(20, 8)  NOT NULL DEFAULT 0,  -- authoritative compounded balance
    -- Configuration snapshot (immutable at trade time)
    configuration_version INTEGER         NOT NULL DEFAULT 1,
    max_loss_pct          DECIMAL(5, 2)   NOT NULL DEFAULT 35.00,
    target_roe_pct        DECIMAL(5, 2)   NOT NULL DEFAULT 60.00,
    -- Lifecycle
    trade_state           VARCHAR(30)     NOT NULL DEFAULT 'OPEN',
    close_reason          VARCHAR(50),
    order_block_upper     DECIMAL(20, 8),
    order_block_lower     DECIMAL(20, 8),
    stop_loss_price       DECIMAL(20, 8),
    take_profit_price     DECIMAL(20, 8),
    entry_order_id        VARCHAR(100),
    sl_order_id           VARCHAR(100),
    tp_order_id           VARCHAR(100),
    opened_at             TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at             TIMESTAMP WITH TIME ZONE,
    created_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trade_records_account
    ON trade_records(trading_account_id);
CREATE INDEX IF NOT EXISTS idx_trade_records_setup_id
    ON trade_records(setup_id);
CREATE INDEX IF NOT EXISTS idx_trade_records_state
    ON trade_records(trade_state);
CREATE INDEX IF NOT EXISTS idx_trade_records_opened_at
    ON trade_records(opened_at DESC);

CREATE TRIGGER update_trade_records_updated_at
    BEFORE UPDATE ON trade_records
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- Safety Assertions (verified at migration time, not at runtime)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_algo_default TEXT;
    v_ks_default   TEXT;
BEGIN
    -- Verify trading_accounts.algo_enabled default is false
    SELECT column_default INTO v_algo_default
    FROM information_schema.columns
    WHERE table_name = 'trading_accounts' AND column_name = 'algo_enabled';

    IF v_algo_default IS NULL OR v_algo_default NOT IN ('false', 'FALSE') THEN
        RAISE EXCEPTION 'SAFETY VIOLATION: trading_accounts.algo_enabled default is not false (got: %)', v_algo_default;
    END IF;

    -- Verify trading_accounts.kill_switch_active default is true
    SELECT column_default INTO v_ks_default
    FROM information_schema.columns
    WHERE table_name = 'trading_accounts' AND column_name = 'kill_switch_active';

    IF v_ks_default IS NULL OR v_ks_default NOT IN ('true', 'TRUE') THEN
        RAISE EXCEPTION 'SAFETY VIOLATION: trading_accounts.kill_switch_active default is not true (got: %)', v_ks_default;
    END IF;

    RAISE NOTICE 'V2 safety assertions passed: algo_enabled=false, kill_switch_active=true';
END $$;
