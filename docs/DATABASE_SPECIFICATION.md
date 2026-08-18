# Database Specification - QuantEdge AI V2

## Overview

PostgreSQL 16+ with Flyway migrations. Multi-tenant schema with application-level ownership enforcement.

## Core Tables

### users
```sql
id UUID PK
email VARCHAR(255) UNIQUE NOT NULL
password_hash VARCHAR(255) NOT NULL
name VARCHAR(100) NOT NULL
is_active BOOLEAN DEFAULT true
email_verified BOOLEAN DEFAULT false
last_login_at TIMESTAMPTZ
created_at TIMESTAMPTZ DEFAULT now()
updated_at TIMESTAMPTZ DEFAULT now()
```

### user_settings
```sql
id UUID PK
user_id UUID FK -> users(id) UNIQUE
timezone VARCHAR(50) DEFAULT 'UTC'
theme VARCHAR(20) DEFAULT 'dark'
notifications_enabled BOOLEAN DEFAULT true
email_notifications BOOLEAN DEFAULT true
push_notifications BOOLEAN DEFAULT false
```

### trading_accounts
```sql
id UUID PK
user_id UUID FK -> users(id)
name VARCHAR(100) NOT NULL
account_type VARCHAR(20) CHECK (PAPER, LIVE)
is_active BOOLEAN DEFAULT true
is_default BOOLEAN DEFAULT false
base_currency VARCHAR(10) DEFAULT 'USDT'
starting_balance DECIMAL(20,8) DEFAULT 0
current_balance DECIMAL(20,8) DEFAULT 0
```

### delta_connections
```sql
id UUID PK
trading_account_id UUID FK -> trading_accounts(id)
environment VARCHAR(20) CHECK (TESTNET, LIVE)
encrypted_api_key TEXT NOT NULL
encrypted_api_secret TEXT NOT NULL
connection_status VARCHAR(20) CHECK (CONNECTED, DISCONNECTED, ERROR)
last_connected_at TIMESTAMPTZ
last_error TEXT
UNIQUE(trading_account_id, environment)
```

### risk_configurations
```sql
id UUID PK
trading_account_id UUID FK -> trading_accounts(id) UNIQUE
risk_per_trade_pct DECIMAL(5,2) DEFAULT 35.00
target_reward_pct DECIMAL(5,2) DEFAULT 60.00
max_leverage INT DEFAULT 100
max_concurrent_trades INT DEFAULT 1
max_daily_loss_pct DECIMAL(5,2)
max_drawdown_pct DECIMAL(5,2)
```

### strategy_configurations
```sql
id UUID PK
trading_account_id UUID FK -> trading_accounts(id) UNIQUE
name VARCHAR(100) DEFAULT 'Default SMC Strategy'
is_active BOOLEAN DEFAULT true
confidence_threshold INT DEFAULT 85
timeframe VARCHAR(10) DEFAULT '1H'
symbols TEXT[] DEFAULT ['BTCUSD.P','ETHUSD.P','SOLUSD.P','XRPUSD.P']
internal_structure_length INT DEFAULT 5
swing_structure_length INT DEFAULT 50
atr_period INT DEFAULT 200
atr_multiplier DECIMAL(4,2) DEFAULT 2.00
ob_width_threshold_pct DECIMAL(5,2) DEFAULT 0.60
opposing_zone_threshold_pct DECIMAL(5,2) DEFAULT 0.50
```

### orders
```sql
id UUID PK
trading_account_id UUID FK -> trading_accounts(id)
strategy_config_id UUID FK -> strategy_configurations(id)
delta_order_id VARCHAR(100)
client_order_id VARCHAR(100) UNIQUE
symbol VARCHAR(20) NOT NULL
side VARCHAR(10) CHECK (BUY, SELL)
order_type VARCHAR(20) CHECK (MARKET, LIMIT, STOP_MARKET, STOP_LIMIT)
status VARCHAR(20) CHECK (PENDING, OPEN, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, EXPIRED)
price DECIMAL(20,8)
stop_price DECIMAL(20,8)
quantity DECIMAL(20,8) NOT NULL
filled_quantity DECIMAL(20,8) DEFAULT 0
average_fill_price DECIMAL(20,8)
leverage INT
reduce_only BOOLEAN DEFAULT false
post_only BOOLEAN DEFAULT false
time_in_force VARCHAR(10) CHECK (GTC, IOC, FOK) DEFAULT 'GTC'
error_message TEXT
placed_at TIMESTAMPTZ DEFAULT now()
filled_at TIMESTAMPTZ
cancelled_at TIMESTAMPTZ
```

### positions
```sql
id UUID PK
trading_account_id UUID FK -> trading_accounts(id)
delta_position_id VARCHAR(100)
symbol VARCHAR(20) NOT NULL
side VARCHAR(10) CHECK (LONG, SHORT)
status VARCHAR(20) CHECK (OPEN, CLOSING, CLOSED, LIQUIDATED) DEFAULT 'OPEN'
entry_price DECIMAL(20,8) NOT NULL
current_price DECIMAL(20,8)
quantity DECIMAL(20,8) NOT NULL
leverage INT NOT NULL
unrealized_pnl DECIMAL(20,8) DEFAULT 0
realized_pnl DECIMAL(20,8) DEFAULT 0
liquidation_price DECIMAL(20,8)
margin_used DECIMAL(20,8) DEFAULT 0
stop_loss_price DECIMAL(20,8)
take_profit_price DECIMAL(20,8)
opened_at TIMESTAMPTZ DEFAULT now()
closed_at TIMESTAMPTZ
```

### order_blocks (Strategy Engine Storage)
```sql
id UUID PK
symbol VARCHAR(20) NOT NULL
timeframe VARCHAR(10) NOT NULL
type VARCHAR(10) CHECK (BULLISH, BEARISH)
top_price DECIMAL(20,8) NOT NULL
bottom_price DECIMAL(20,8) NOT NULL
formation_candle_time TIMESTAMPTZ NOT NULL
formation_candle_open DECIMAL(20,8) NOT NULL
formation_candle_high DECIMAL(20,8) NOT NULL
formation_candle_low DECIMAL(20,8) NOT NULL
formation_candle_close DECIMAL(20,8) NOT NULL
state VARCHAR(20) CHECK (FRESH, TOUCHED, USED, INVALIDATED) DEFAULT 'FRESH'
touch_count INT DEFAULT 0
invalidated_at TIMESTAMPTZ
invalidated_by_price DECIMAL(20,8)
bos_choch_type VARCHAR(10) CHECK (BOS, CHOCH)
swing_trend VARCHAR(10) CHECK (BULLISH, BEARISH, RANGING)
internal_trend VARCHAR(10) CHECK (BULLISH, BEARISH, RANGING)
confidence_score INT
```

### journal_entries
```sql
id UUID PK
user_id UUID FK -> users(id)
trading_account_id UUID FK -> trading_accounts(id)
title VARCHAR(255) NOT NULL
content TEXT
trade_symbol VARCHAR(20)
trade_side VARCHAR(10) CHECK (BUY, SELL)
trade_entry_price DECIMAL(20,8)
trade_exit_price DECIMAL(20,8)
trade_pnl DECIMAL(20,8)
trade_r_multiple DECIMAL(10,2)
emotions TEXT[]
tags TEXT[]
entry_date DATE DEFAULT CURRENT_DATE
```

### audit_logs
```sql
id UUID PK
user_id UUID FK -> users(id)
trading_account_id UUID FK -> trading_accounts(id)
action VARCHAR(100) NOT NULL
resource_type VARCHAR(50)
resource_id UUID
old_values JSONB
new_values JSONB
ip_address INET
user_agent TEXT
created_at TIMESTAMPTZ DEFAULT now()
```

## Indexes

- `idx_users_email` on users(email)
- `idx_trading_accounts_user` on trading_accounts(user_id)
- `idx_orders_account_status` on orders(trading_account_id, status)
- `idx_positions_account_status` on positions(trading_account_id, status)
- `idx_order_blocks_symbol_tf` on order_blocks(symbol, timeframe)
- `idx_audit_logs_user_time` on audit_logs(user_id, created_at)

## Triggers

- `update_updated_at_column()` on all tables with updated_at

## Row Level Security

**NOT YET IMPLEMENTED** - Planned for hardening phase.

Current isolation is enforced at the application layer:
- All repository queries include user_id/trading_account_id filters
- Service layer validates ownership before any operation
- No cross-user data access possible through API

Future RLS implementation:
```sql
ALTER TABLE trading_accounts ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON trading_accounts
  USING (user_id = current_setting('app.current_user_id')::UUID);
```

## Migration Strategy

- Flyway versioned migrations (V1__, V2__, etc.) - ONLY authoritative mechanism
- Baseline: V1__initial_schema.sql
- Rollback: Manual SQL scripts
- Test: Separate test database with test profile
- **No duplicate initialization via PostgreSQL docker-entrypoint-initdb.d**