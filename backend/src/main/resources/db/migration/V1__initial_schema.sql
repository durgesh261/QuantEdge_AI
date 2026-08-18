-- QuantEdge AI V2 - Initial Schema
-- V1__initial_schema.sql

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_is_active ON users(is_active);

-- User Settings table
CREATE TABLE user_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timezone VARCHAR(50) NOT NULL DEFAULT 'UTC',
    theme VARCHAR(20) NOT NULL DEFAULT 'dark',
    notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    email_notifications BOOLEAN NOT NULL DEFAULT TRUE,
    push_notifications BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id)
);

-- Trading Accounts table
CREATE TABLE trading_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    account_type VARCHAR(20) NOT NULL CHECK (account_type IN ('PAPER', 'LIVE')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    base_currency VARCHAR(10) NOT NULL DEFAULT 'USDT',
    starting_balance DECIMAL(20, 8) NOT NULL DEFAULT 0,
    current_balance DECIMAL(20, 8) NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_trading_accounts_user_id ON trading_accounts(user_id);
CREATE INDEX idx_trading_accounts_is_active ON trading_accounts(is_active);
CREATE INDEX idx_trading_accounts_account_type ON trading_accounts(account_type);

-- Delta Connections table
CREATE TABLE delta_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id UUID NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    environment VARCHAR(20) NOT NULL CHECK (environment IN ('TESTNET', 'LIVE')),
    encrypted_api_key TEXT NOT NULL,
    encrypted_api_secret TEXT NOT NULL,
    connection_status VARCHAR(20) NOT NULL DEFAULT 'DISCONNECTED' CHECK (connection_status IN ('CONNECTED', 'DISCONNECTED', 'ERROR')),
    last_connected_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trading_account_id, environment)
);

CREATE INDEX idx_delta_connections_trading_account ON delta_connections(trading_account_id);

-- Risk Configurations table
CREATE TABLE risk_configurations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id UUID NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    risk_per_trade_percent DECIMAL(5, 2) NOT NULL DEFAULT 35.00 CHECK (risk_per_trade_percent > 0 AND risk_per_trade_percent <= 100),
    target_reward_percent DECIMAL(5, 2) NOT NULL DEFAULT 60.00 CHECK (target_reward_percent > 0),
    max_leverage INTEGER NOT NULL DEFAULT 100 CHECK (max_leverage > 0 AND max_leverage <= 100),
    max_concurrent_trades INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrent_trades > 0),
    max_daily_loss_percent DECIMAL(5, 2) CHECK (max_daily_loss_percent > 0 AND max_daily_loss_percent <= 100),
    max_drawdown_percent DECIMAL(5, 2) CHECK (max_drawdown_percent > 0 AND max_drawdown_percent <= 100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trading_account_id)
);

-- Strategy Configurations table
CREATE TABLE strategy_configurations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id UUID NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL DEFAULT 'Default SMC Strategy',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    confidence_threshold INTEGER NOT NULL DEFAULT 85 CHECK (confidence_threshold >= 0 AND confidence_threshold <= 100),
    timeframe VARCHAR(10) NOT NULL DEFAULT '1H',
    symbols TEXT[] NOT NULL DEFAULT ARRAY['BTCUSD.P', 'ETHUSD.P', 'SOLUSD.P', 'XRPUSD.P'],
    internal_structure_length INTEGER NOT NULL DEFAULT 5,
    swing_structure_length INTEGER NOT NULL DEFAULT 50,
    atr_period INTEGER NOT NULL DEFAULT 200,
    atr_multiplier DECIMAL(4, 2) NOT NULL DEFAULT 2.00,
    ob_width_threshold_percent DECIMAL(5, 2) NOT NULL DEFAULT 0.60,
    opposing_zone_threshold_percent DECIMAL(5, 2) NOT NULL DEFAULT 0.50,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trading_account_id)
);

-- Orders table
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id UUID NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    strategy_config_id UUID REFERENCES strategy_configurations(id) ON DELETE SET NULL,
    delta_order_id VARCHAR(100),
    client_order_id VARCHAR(100) UNIQUE,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type VARCHAR(20) NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP_MARKET', 'STOP_LIMIT')),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED')),
    price DECIMAL(20, 8),
    stop_price DECIMAL(20, 8),
    quantity DECIMAL(20, 8) NOT NULL,
    filled_quantity DECIMAL(20, 8) NOT NULL DEFAULT 0,
    average_fill_price DECIMAL(20, 8),
    leverage INTEGER,
    reduce_only BOOLEAN NOT NULL DEFAULT FALSE,
    post_only BOOLEAN NOT NULL DEFAULT FALSE,
    time_in_force VARCHAR(10) NOT NULL DEFAULT 'GTC' CHECK (time_in_force IN ('GTC', 'IOC', 'FOK')),
    error_message TEXT,
    placed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    filled_at TIMESTAMP WITH TIME ZONE,
    cancelled_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_orders_trading_account ON orders(trading_account_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_delta_order_id ON orders(delta_order_id);
CREATE INDEX idx_orders_client_order_id ON orders(client_order_id);

-- Positions table
CREATE TABLE positions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id UUID NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    delta_position_id VARCHAR(100),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSING', 'CLOSED', 'LIQUIDATED')),
    entry_price DECIMAL(20, 8) NOT NULL,
    current_price DECIMAL(20, 8),
    quantity DECIMAL(20, 8) NOT NULL,
    leverage INTEGER NOT NULL,
    unrealized_pnl DECIMAL(20, 8) NOT NULL DEFAULT 0,
    realized_pnl DECIMAL(20, 8) NOT NULL DEFAULT 0,
    liquidation_price DECIMAL(20, 8),
    margin_used DECIMAL(20, 8) NOT NULL DEFAULT 0,
    stop_loss_price DECIMAL(20, 8),
    take_profit_price DECIMAL(20, 8),
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_positions_trading_account ON positions(trading_account_id);
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_delta_position_id ON positions(delta_position_id);

-- Order Blocks table (for strategy engine)
CREATE TABLE order_blocks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    type VARCHAR(10) NOT NULL CHECK (type IN ('BULLISH', 'BEARISH')),
    top_price DECIMAL(20, 8) NOT NULL,
    bottom_price DECIMAL(20, 8) NOT NULL,
    formation_candle_time TIMESTAMP WITH TIME ZONE NOT NULL,
    formation_candle_open DECIMAL(20, 8) NOT NULL,
    formation_candle_high DECIMAL(20, 8) NOT NULL,
    formation_candle_low DECIMAL(20, 8) NOT NULL,
    formation_candle_close DECIMAL(20, 8) NOT NULL,
    touch_count INTEGER NOT NULL DEFAULT 0 CHECK (touch_count >= 0),
    is_used BOOLEAN NOT NULL DEFAULT FALSE,
    is_invalidated BOOLEAN NOT NULL DEFAULT FALSE,
    invalidated_at TIMESTAMP WITH TIME ZONE,
    invalidated_by_price DECIMAL(20, 8),
    bos_choch_type VARCHAR(10) CHECK (bos_choch_type IN ('BOS', 'CHOCH')),
    swing_trend VARCHAR(10) CHECK (swing_trend IN ('BULLISH', 'BEARISH', 'RANGING')),
    internal_trend VARCHAR(10) CHECK (internal_trend IN ('BULLISH', 'BEARISH', 'RANGING')),
    confidence_score INTEGER CHECK (confidence_score >= 0 AND confidence_score <= 100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_order_blocks_symbol_timeframe ON order_blocks(symbol, timeframe);
CREATE INDEX idx_order_blocks_type ON order_blocks(type);
CREATE INDEX idx_order_blocks_is_used ON order_blocks(is_used);
CREATE INDEX idx_order_blocks_is_invalidated ON order_blocks(is_invalidated);
CREATE INDEX idx_order_blocks_formation_time ON order_blocks(formation_candle_time);

-- Journal Entries table
CREATE TABLE journal_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trading_account_id UUID REFERENCES trading_accounts(id) ON DELETE SET NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT,
    trade_symbol VARCHAR(20),
    trade_side VARCHAR(10) CHECK (trade_side IN ('BUY', 'SELL')),
    trade_entry_price DECIMAL(20, 8),
    trade_exit_price DECIMAL(20, 8),
    trade_pnl DECIMAL(20, 8),
    trade_r_multiple DECIMAL(10, 2),
    emotions TEXT[],
    tags TEXT[],
    entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_journal_entries_user_id ON journal_entries(user_id);
CREATE INDEX idx_journal_entries_entry_date ON journal_entries(entry_date);
CREATE INDEX idx_journal_entries_trading_account ON journal_entries(trading_account_id);

-- Audit Logs table
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    trading_account_id UUID REFERENCES trading_accounts(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id UUID,
    old_values JSONB,
    new_values JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_trading_account ON audit_logs(trading_account_id);
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply updated_at triggers
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_user_settings_updated_at BEFORE UPDATE ON user_settings FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_trading_accounts_updated_at BEFORE UPDATE ON trading_accounts FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_delta_connections_updated_at BEFORE UPDATE ON delta_connections FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_risk_configurations_updated_at BEFORE UPDATE ON risk_configurations FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_strategy_configurations_updated_at BEFORE UPDATE ON strategy_configurations FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_positions_updated_at BEFORE UPDATE ON positions FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_order_blocks_updated_at BEFORE UPDATE ON order_blocks FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_journal_entries_updated_at BEFORE UPDATE ON journal_entries FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();