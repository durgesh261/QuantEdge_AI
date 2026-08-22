-- =============================================================================
-- QuantEdge AI — Initial Database Schema
-- Migration: V1__initial_schema.sql
-- Compatible: PostgreSQL 15+
-- =============================================================================
-- CRITICAL SAFETY DEFAULTS:
--   algo_enabled      = false  (algorithmic trading OFF by default)
--   kill_switch_active = true  (emergency kill switch ON by default)
-- These must NEVER be changed here. Operator action required to modify.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                  VARCHAR(36)  PRIMARY KEY,
    email               VARCHAR(255) NOT NULL UNIQUE,
    username            VARCHAR(100) NOT NULL UNIQUE,
    hashed_password     VARCHAR(255),
    role                VARCHAR(20)  NOT NULL DEFAULT 'USER',
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    email_verified      BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ---------------------------------------------------------------------------
-- 2. Trading Accounts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trading_accounts (
    id                  VARCHAR(36)  PRIMARY KEY,
    user_id             VARCHAR(36)  NOT NULL REFERENCES users(id),
    name                VARCHAR(100) NOT NULL,
    account_type        VARCHAR(20)  NOT NULL DEFAULT 'LIVE',
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    is_default          BOOLEAN      NOT NULL DEFAULT FALSE,
    base_currency       VARCHAR(10)  NOT NULL DEFAULT 'USDT',
    starting_balance    NUMERIC(20, 8) NOT NULL DEFAULT 0,
    current_balance     NUMERIC(20, 8) NOT NULL DEFAULT 0,
    total_equity        NUMERIC(20, 8)          DEFAULT 0,
    available_balance   NUMERIC(20, 8)          DEFAULT 0,
    margin_used         NUMERIC(20, 8)          DEFAULT 0,
    -- CRITICAL FAIL-SAFE DEFAULTS — never alter these defaults
    algo_enabled        BOOLEAN      NOT NULL DEFAULT FALSE,
    kill_switch_active  BOOLEAN      NOT NULL DEFAULT TRUE,
    last_synced_at      TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trading_accounts_user_id ON trading_accounts(user_id);

-- ---------------------------------------------------------------------------
-- 3. Delta Exchange Connections
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS delta_connections (
    id                   VARCHAR(36)  PRIMARY KEY,
    trading_account_id   VARCHAR(36)  NOT NULL REFERENCES trading_accounts(id),
    environment          VARCHAR(20)  NOT NULL DEFAULT 'LIVE',
    encrypted_api_key    TEXT         NOT NULL,
    encrypted_api_secret TEXT         NOT NULL,
    connection_status    VARCHAR(20)  NOT NULL DEFAULT 'DISCONNECTED',
    last_connected_at    TIMESTAMPTZ,
    last_error           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_delta_connections_trading_account ON delta_connections(trading_account_id);

-- ---------------------------------------------------------------------------
-- 4. Risk Configurations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_configurations (
    id                      VARCHAR(36)    PRIMARY KEY,
    trading_account_id      VARCHAR(36)    NOT NULL UNIQUE REFERENCES trading_accounts(id),
    version                 INTEGER        NOT NULL DEFAULT 1,
    take_profit_percent     NUMERIC(5, 2)  NOT NULL DEFAULT 60.00,
    stop_loss_percent       NUMERIC(5, 2)  NOT NULL DEFAULT 1.00,
    risk_per_trade_percent  NUMERIC(5, 2)  NOT NULL DEFAULT 35.00,
    target_reward_percent   NUMERIC(5, 2)  NOT NULL DEFAULT 60.00,
    max_leverage            INTEGER        NOT NULL DEFAULT 100,
    max_concurrent_trades   INTEGER        NOT NULL DEFAULT 1,
    minimum_risk_reward     NUMERIC(5, 2)  NOT NULL DEFAULT 1.50,
    max_daily_loss_percent  NUMERIC(5, 2)           DEFAULT 5.00,
    max_drawdown_percent    NUMERIC(5, 2),
    -- CRITICAL FAIL-SAFE DEFAULTS
    algo_enabled            BOOLEAN        NOT NULL DEFAULT FALSE,
    kill_switch_active      BOOLEAN        NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 5. Strategy Setup Records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_setups (
    id                   VARCHAR(36)    PRIMARY KEY,
    trading_account_id   VARCHAR(36)    REFERENCES trading_accounts(id),
    setup_id             VARCHAR(100)   NOT NULL UNIQUE,
    symbol               VARCHAR(20)    NOT NULL,
    direction            VARCHAR(10)    NOT NULL,
    timeframe            VARCHAR(10)    NOT NULL DEFAULT '1h',
    setup_state          VARCHAR(30)    NOT NULL DEFAULT 'TRADE_SETUP_READY',
    strategy_name        VARCHAR(50)    NOT NULL DEFAULT 'SMC',
    strategy_version     VARCHAR(20)    NOT NULL DEFAULT '2.1',
    configuration_version INTEGER               DEFAULT 1,
    entry_price          NUMERIC(20, 8) NOT NULL,
    stop_loss            NUMERIC(20, 8) NOT NULL,
    take_profit          NUMERIC(20, 8) NOT NULL,
    risk_distance        NUMERIC(20, 8),
    reward_distance      NUMERIC(20, 8),
    risk_reward          NUMERIC(10, 4),
    confidence           NUMERIC(5, 2),
    expires_at           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_setups_setup_id ON strategy_setups(setup_id);
CREATE INDEX IF NOT EXISTS idx_strategy_setups_account ON strategy_setups(trading_account_id);

-- ---------------------------------------------------------------------------
-- 6. Orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id                   VARCHAR(36)    PRIMARY KEY,
    trading_account_id   VARCHAR(36)    NOT NULL REFERENCES trading_accounts(id),
    setup_id             VARCHAR(100),
    delta_order_id       VARCHAR(100),
    client_order_id      VARCHAR(100)   UNIQUE,
    symbol               VARCHAR(20)    NOT NULL,
    side                 VARCHAR(10)    NOT NULL,
    order_type           VARCHAR(20)    NOT NULL,
    status               VARCHAR(20)    NOT NULL DEFAULT 'PENDING',
    price                NUMERIC(20, 8),
    stop_price           NUMERIC(20, 8),
    quantity             NUMERIC(20, 8) NOT NULL,
    filled_quantity      NUMERIC(20, 8) NOT NULL DEFAULT 0,
    average_fill_price   NUMERIC(20, 8),
    leverage             INTEGER,
    reduce_only          BOOLEAN        NOT NULL DEFAULT FALSE,
    post_only            BOOLEAN        NOT NULL DEFAULT FALSE,
    time_in_force        VARCHAR(10)    NOT NULL DEFAULT 'GTC',
    error_message        TEXT,
    placed_at            TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    filled_at            TIMESTAMPTZ,
    cancelled_at         TIMESTAMPTZ,
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_trading_account ON orders(trading_account_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_order_id ON orders(client_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_delta_order_id ON orders(delta_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_setup_id ON orders(setup_id);

-- ---------------------------------------------------------------------------
-- 7. Positions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
    id                   VARCHAR(36)    PRIMARY KEY,
    trading_account_id   VARCHAR(36)    NOT NULL REFERENCES trading_accounts(id),
    delta_position_id    VARCHAR(100),
    symbol               VARCHAR(20)    NOT NULL,
    side                 VARCHAR(10)    NOT NULL,
    status               VARCHAR(20)    NOT NULL DEFAULT 'OPEN',
    entry_price          NUMERIC(20, 8) NOT NULL,
    current_price        NUMERIC(20, 8),
    quantity             NUMERIC(20, 8) NOT NULL,
    leverage             INTEGER        NOT NULL,
    unrealized_pnl       NUMERIC(20, 8) NOT NULL DEFAULT 0,
    realized_pnl         NUMERIC(20, 8) NOT NULL DEFAULT 0,
    liquidation_price    NUMERIC(20, 8),
    margin_used          NUMERIC(20, 8) NOT NULL DEFAULT 0,
    stop_loss_price      NUMERIC(20, 8),
    take_profit_price    NUMERIC(20, 8),
    opened_at            TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    closed_at            TIMESTAMPTZ,
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_positions_trading_account ON positions(trading_account_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_delta_position_id ON positions(delta_position_id);

-- ---------------------------------------------------------------------------
-- 8. Audit Logs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id                   VARCHAR(36)  PRIMARY KEY,
    user_id              VARCHAR(36),
    trading_account_id   VARCHAR(36),
    action               VARCHAR(100) NOT NULL,
    resource_type        VARCHAR(100),
    resource_id          VARCHAR(100),
    details              TEXT,
    status               VARCHAR(20)  DEFAULT 'SUCCESS',
    ip_address           VARCHAR(50),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_trading_account_id ON audit_logs(trading_account_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);

-- ---------------------------------------------------------------------------
-- Safety Assertions (verified at migration time)
-- ---------------------------------------------------------------------------
-- Confirm fail-safe defaults are correctly set on new-row inserts
DO $$
BEGIN
    -- Verify trading_accounts defaults
    IF (SELECT column_default FROM information_schema.columns
        WHERE table_name='trading_accounts' AND column_name='algo_enabled') != 'false'
    THEN
        RAISE EXCEPTION 'SAFETY VIOLATION: trading_accounts.algo_enabled default must be false';
    END IF;

    IF (SELECT column_default FROM information_schema.columns
        WHERE table_name='trading_accounts' AND column_name='kill_switch_active') != 'true'
    THEN
        RAISE EXCEPTION 'SAFETY VIOLATION: trading_accounts.kill_switch_active default must be true';
    END IF;
END $$;
