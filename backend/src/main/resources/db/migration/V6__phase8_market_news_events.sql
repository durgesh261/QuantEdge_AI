-- ============================================================================
-- V6: Phase 8 — Market Data, Financial News, Economic Events & Notifications
-- ============================================================================

-- 1. News Articles Table (7-Day Retention Policy)
CREATE TABLE IF NOT EXISTS news_articles (
    id                VARCHAR(36)    PRIMARY KEY,
    title             VARCHAR(500)   NOT NULL,
    summary           TEXT,
    source            VARCHAR(100)   NOT NULL,
    source_url        VARCHAR(1000)  NOT NULL,
    category          VARCHAR(50)    NOT NULL,
    importance        VARCHAR(20)    NOT NULL DEFAULT 'MEDIUM',
    relevant_symbols  VARCHAR(255),
    sentiment         VARCHAR(20)    DEFAULT 'NEUTRAL',
    fingerprint       VARCHAR(64)    NOT NULL UNIQUE,
    published_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_published_at ON news_articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_expires_at ON news_articles(expires_at);
CREATE INDEX IF NOT EXISTS idx_news_category ON news_articles(category);
CREATE INDEX IF NOT EXISTS idx_news_importance ON news_articles(importance);
CREATE INDEX IF NOT EXISTS idx_news_fingerprint ON news_articles(fingerprint);

-- 2. Economic Events Table (15-Day Window + 24-Hour Post-Event Retention)
CREATE TABLE IF NOT EXISTS economic_events (
    id                VARCHAR(36)    PRIMARY KEY,
    event_name        VARCHAR(255)   NOT NULL,
    country           VARCHAR(10)    NOT NULL,
    currency          VARCHAR(10)    NOT NULL,
    category          VARCHAR(50)    NOT NULL,
    importance        VARCHAR(20)    NOT NULL DEFAULT 'MEDIUM',
    scheduled_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    previous_value    VARCHAR(50),
    forecast_value    VARCHAR(50),
    actual_value      VARCHAR(50),
    status            VARCHAR(30)    NOT NULL DEFAULT 'UPCOMING',
    source            VARCHAR(100),
    source_url        VARCHAR(1000),
    provider_event_id VARCHAR(100)   UNIQUE,
    expires_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_economic_scheduled_at ON economic_events(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_economic_expires_at ON economic_events(expires_at);
CREATE INDEX IF NOT EXISTS idx_economic_country ON economic_events(country);
CREATE INDEX IF NOT EXISTS idx_economic_currency ON economic_events(currency);
CREATE INDEX IF NOT EXISTS idx_economic_importance ON economic_events(importance);
CREATE INDEX IF NOT EXISTS idx_economic_status ON economic_events(status);

-- 3. In-App Notification Events Table
CREATE TABLE IF NOT EXISTS notification_events (
    id                VARCHAR(36)    PRIMARY KEY,
    user_id           UUID           REFERENCES users(id) ON DELETE CASCADE,
    type              VARCHAR(50)    NOT NULL,
    title             VARCHAR(255)   NOT NULL,
    message           TEXT           NOT NULL,
    reference_id      VARCHAR(100),
    severity          VARCHAR(20)    NOT NULL DEFAULT 'INFO',
    is_read           BOOLEAN        NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notification_events(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notification_events(type);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notification_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notification_events(is_read);
