-- V8: Add refresh_sessions table (persistent refresh-token revocation + rotation)
CREATE TABLE IF NOT EXISTS refresh_sessions (
    id               VARCHAR(36)  NOT NULL PRIMARY KEY,
    token_hash       VARCHAR(64)  NOT NULL UNIQUE,
    user_id          VARCHAR(36)  NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ  NOT NULL,
    revoked_at       TIMESTAMPTZ,
    replaced_by_hash VARCHAR(64),
    CONSTRAINT fk_rs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rs_token_hash ON refresh_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_rs_user_id    ON refresh_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_rs_expires_at ON refresh_sessions(expires_at);
