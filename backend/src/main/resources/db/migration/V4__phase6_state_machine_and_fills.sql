-- =============================================================================
-- QuantEdge AI — V4 Schema — Phase 6: Authoritative Trading State
-- Migration: V4__phase6_state_machine_and_fills.sql
-- Compatible: PostgreSQL 15+ | Flyway baseline >= V3
-- =============================================================================
-- PURPOSE:
--   1. Align the orders.status CHECK constraint with the full deterministic
--      state machine used by OrderExecutionService (adds SUBMITTED, FAILED, UNKNOWN).
--   2. Add submitted_at and reconciliation_state columns to orders.
--   3. Create order_fills table for individual fill recording and deduplication.
--   4. Add setup_id, entry_order_id, close_order_id, reconciliation_state
--      columns to the existing positions table.
--   5. Add a unique index on order_fills.exchange_fill_id for dedup enforcement.
--
-- SAFETY:
--   No existing data is deleted or modified.
--   All column additions use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.
--   Constraint changes use DROP CONSTRAINT IF EXISTS before adding the new one.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. orders — expand status CHECK constraint to include SUBMITTED, FAILED, UNKNOWN
--    Current V1 constraint only allows:
--    PENDING | OPEN | PARTIALLY_FILLED | FILLED | CANCELLED | REJECTED | EXPIRED
--    Phase 6 state machine requires:
--    CREATED | SUBMISSION_PENDING | SUBMITTED | OPEN | PARTIALLY_FILLED | FILLED |
--    CANCEL_PENDING | CANCELLED | REJECTED | FAILED | UNKNOWN | EXPIRED
-- ---------------------------------------------------------------------------
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;

ALTER TABLE orders
    ADD CONSTRAINT orders_status_check
        CHECK (status IN (
            'CREATED',
            'SUBMISSION_PENDING',
            'SUBMITTED',
            'OPEN',
            'PARTIALLY_FILLED',
            'FILLED',
            'CANCEL_PENDING',
            'CANCELLED',
            'REJECTED',
            'FAILED',
            'UNKNOWN',
            'EXPIRED',
            'PENDING'
        ));

-- ---------------------------------------------------------------------------
-- 2. orders — add submitted_at and reconciliation_state columns
-- ---------------------------------------------------------------------------
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS submitted_at          TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS reconciliation_state  VARCHAR(30) NOT NULL DEFAULT 'NONE';

CREATE INDEX IF NOT EXISTS idx_orders_reconciliation_state
    ON orders(reconciliation_state)
    WHERE reconciliation_state != 'NONE';

-- ---------------------------------------------------------------------------
-- 3. order_fills — individual fill records per order
--    exchange_fill_id is UNIQUE to prevent duplicate fill ingestion.
--    order_id is a soft reference (VARCHAR) to orders.client_order_id to
--    avoid FK complications during reconciliation when orders may not exist yet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_fills (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id  UUID            NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    order_id            UUID            REFERENCES orders(id) ON DELETE SET NULL,
    exchange_fill_id    VARCHAR(100)    NOT NULL,
    client_order_id     VARCHAR(100),
    delta_order_id      VARCHAR(100),
    symbol              VARCHAR(20)     NOT NULL,
    side                VARCHAR(10)     NOT NULL CHECK (side IN ('BUY', 'SELL')),
    fill_quantity       DECIMAL(20, 8)  NOT NULL CHECK (fill_quantity > 0),
    fill_price          DECIMAL(20, 8)  NOT NULL CHECK (fill_price > 0),
    fee                 DECIMAL(20, 8)  NOT NULL DEFAULT 0,
    fee_asset           VARCHAR(20),
    filled_at           TIMESTAMP WITH TIME ZONE NOT NULL,
    raw_exchange_data   TEXT,                           -- raw JSON from exchange for audit
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Deduplication: exactly one row per exchange fill ID
CREATE UNIQUE INDEX IF NOT EXISTS idx_order_fills_exchange_fill_id
    ON order_fills(exchange_fill_id);

CREATE INDEX IF NOT EXISTS idx_order_fills_order_id
    ON order_fills(order_id);

CREATE INDEX IF NOT EXISTS idx_order_fills_trading_account
    ON order_fills(trading_account_id);

CREATE INDEX IF NOT EXISTS idx_order_fills_client_order_id
    ON order_fills(client_order_id);

CREATE INDEX IF NOT EXISTS idx_order_fills_filled_at
    ON order_fills(filled_at DESC);

CREATE TRIGGER update_order_fills_updated_at
    BEFORE UPDATE ON order_fills
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- 4. positions — add missing Phase 6 columns
--    The positions table already exists from V1 but has no JPA entity.
--    Phase 6 adds columns needed by the reconciliation and state model.
-- ---------------------------------------------------------------------------
ALTER TABLE positions
    ADD COLUMN IF NOT EXISTS setup_id              VARCHAR(100),
    ADD COLUMN IF NOT EXISTS entry_order_id        VARCHAR(100),
    ADD COLUMN IF NOT EXISTS close_order_id        VARCHAR(100),
    ADD COLUMN IF NOT EXISTS reconciliation_state  VARCHAR(30) NOT NULL DEFAULT 'AUTHORITATIVE',
    ADD COLUMN IF NOT EXISTS last_reconciled_at    TIMESTAMP WITH TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_positions_setup_id
    ON positions(setup_id);

CREATE INDEX IF NOT EXISTS idx_positions_entry_order_id
    ON positions(entry_order_id);

CREATE INDEX IF NOT EXISTS idx_positions_reconciliation
    ON positions(reconciliation_state)
    WHERE reconciliation_state != 'AUTHORITATIVE';

-- ---------------------------------------------------------------------------
-- 5. active_trade_locks — add client_order_id linkage for reconciliation
-- ---------------------------------------------------------------------------
ALTER TABLE active_trade_locks
    ADD COLUMN IF NOT EXISTS client_order_id VARCHAR(100);

-- ---------------------------------------------------------------------------
-- Safety assertion — verify new order_fills table was created
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'order_fills'
    ) THEN
        RAISE EXCEPTION 'SAFETY VIOLATION: order_fills table was not created by V4 migration';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'submitted_at'
    ) THEN
        RAISE EXCEPTION 'SAFETY VIOLATION: orders.submitted_at column was not added by V4 migration';
    END IF;

    RAISE NOTICE 'V4 Phase 6 safety assertions passed: order_fills table exists, orders.submitted_at exists';
END $$;
