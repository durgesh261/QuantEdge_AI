-- ============================================================================
-- V5: Phase 7.5 — AI Intelligence & Signal Enrichment Table
-- ============================================================================
-- Establishes a dedicated, tenant-isolated AI signal enrichment table linked
-- to strategy_setups(setup_id) to store pattern intelligence, signal scoring,
-- market context, regime detection, and confidence modeling.
--
-- INVARIANTS:
-- - All scores are strictly constrained between 0.00 and 100.00.
-- - Authoritative SMC values in strategy_setups remain immutable.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ai_signal_enrichments (
    id                     UUID           PRIMARY KEY DEFAULT uuid_generate_v4(),
    trading_account_id     UUID           NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
    setup_id               VARCHAR(100)   NOT NULL,
    symbol                 VARCHAR(20)    NOT NULL,
    direction              VARCHAR(10)    NOT NULL,
    intelligence_version   VARCHAR(30)    NOT NULL DEFAULT '1.0.0-baseline',
    pattern_score          NUMERIC(5, 2)  NOT NULL,
    signal_score           NUMERIC(5, 2)  NOT NULL,
    confidence             NUMERIC(5, 2)  NOT NULL,
    market_regime          VARCHAR(50)    NOT NULL,
    market_context         VARCHAR(100)   NOT NULL,
    model_metadata         TEXT,
    feature_summary        TEXT,
    generated_at           TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at             TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_ai_pattern_score CHECK (pattern_score >= 0.00 AND pattern_score <= 100.00),
    CONSTRAINT chk_ai_signal_score  CHECK (signal_score >= 0.00 AND signal_score <= 100.00),
    CONSTRAINT chk_ai_confidence    CHECK (confidence >= 0.00 AND confidence <= 100.00)
);

CREATE INDEX IF NOT EXISTS idx_ai_enrichment_setup_id ON ai_signal_enrichments(setup_id);
CREATE INDEX IF NOT EXISTS idx_ai_enrichment_account ON ai_signal_enrichments(trading_account_id);
CREATE INDEX IF NOT EXISTS idx_ai_enrichment_generated_at ON ai_signal_enrichments(generated_at);
